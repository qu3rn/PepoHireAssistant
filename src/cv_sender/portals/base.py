"""Abstract base class for portal-specific form fillers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Locator, Page

from cv_sender.browser import browser_session, navigate
from cv_sender.config import Profile, Settings
from cv_sender.models import FillResult, FillStatus, Offer

# ---------------------------------------------------------------------------
# Consent keyword lists
# ---------------------------------------------------------------------------

_APPLY_BUTTON_TEXTS = [
    "Apply",
    "Apply now",
    "Apply for this job",
    "Aplikuj",
    "Aplikuj teraz",
    "Wyślij CV",
    "Wyślij aplikację",
    "Aplikuj na to stanowisko",
]

_REQUIRED_CONSENT_KEYWORDS = [
    "przetwarzanie",
    "consent",
    "rodo",
    "gdpr",
    "privacy",
    "prywatno",
    "personal data",
    "danych osobowych",
    "wyrażam zgodę",
    "polityką prywatności",
]

_MARKETING_KEYWORDS = [
    "marketing",
    "newsletter",
    "commercial",
    "przyszłych rekrutacji",
    "future recruitment",
    "informacji handlowych",
]


class BasePortalFiller(ABC):
    """Common interface for all portal fillers.

    Subclasses implement :meth:`fill_form` which receives the already-loaded
    Playwright *page*.  Use the rich helper methods to fill fields and call
    :meth:`fill` (instead of :meth:`run`) to get a structured :class:`FillResult`.
    """

    source: str = "generic"

    def __init__(self, profile: Profile, settings: Settings, headless: bool = False) -> None:
        self.profile = profile
        self.settings = settings
        self.headless = headless
        self._result: FillResult | None = None

    # ── Public interface ──────────────────────────────────────────────────────

    def can_handle(self, offer: Offer) -> bool:
        """Return True if this filler can handle the given offer."""
        return True

    def fill(self, offer: Offer, *, auto_submit: bool = False) -> FillResult:
        """Open the browser, fill the application form, return a :class:`FillResult`.

        The form is **never** submitted automatically regardless of *auto_submit*.
        This flag exists only for future supervised flows and must remain False.
        """
        headless = self.settings.form_filling.headless or self.headless
        slow_mo = self.settings.form_filling.slow_mo_ms

        result = FillResult(
            status=FillStatus.FAILED,
            source=self.__class__.source,
            offer_id=offer.id,
            url=offer.url,
        )
        self._result = result
        _page: Page | None = None

        try:
            with browser_session(headless=headless, slow_mo=slow_mo) as (page, _):
                _page = page
                navigate(page, offer.url)
                self.fill_form(page)
                self._finalize_status()
        except Exception as exc:  # noqa: BLE001
            result.status = FillStatus.FAILED
            result.error = str(exc)
            if self.settings.form_filling.debug and _page is not None:
                self._save_debug_screenshot(_page, offer.id)
        finally:
            self._result = None

        return result

    def run(self, url: str, *, wait_for_review: bool = True) -> None:
        """Legacy CLI interface: open *url*, fill the form, pause for review.

        Use :meth:`fill` when a structured :class:`FillResult` is needed.
        """
        with browser_session(headless=self.headless) as (page, _browser):
            navigate(page, url)
            self.fill_form(page)
            self._print_review_prompt()
            if wait_for_review:
                input("Press ENTER when you are ready to close the browser…")

    @abstractmethod
    def fill_form(self, page: Page) -> None:
        """Fill the application form on the already-loaded *page*."""

    # ── Rich fill helpers (call from fill_form) ───────────────────────────────

    def wait_for_form_ready(self, page: Page, timeout: int = 10_000) -> None:
        """Wait until the page network is idle."""
        try:
            page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:  # noqa: BLE001
            pass

    def click_apply_button(self, page: Page) -> bool:
        """Click the primary Apply button or link.  Returns True when found."""
        for text in _APPLY_BUTTON_TEXTS:
            for role in ("button", "link"):
                try:
                    el = page.get_by_role(role, name=text)  # type: ignore[arg-type]
                    if el.count() and el.first.is_visible():
                        el.first.click()
                        self.wait_for_form_ready(page)
                        return True
                except Exception:  # noqa: BLE001
                    pass
        return False

    def fill_text_field_by_label_or_placeholder(
        self,
        page: Page,
        field_name: str,
        value: str,
        *labels: str,
    ) -> bool:
        """Fill a text/email/tel field identified by label or placeholder text.

        Strategy: get_by_label → get_by_placeholder → attribute-based selectors.
        Updates *_result* tracking.  Returns True if filled.
        """
        for label in labels:
            # 1. Accessible label
            try:
                loc = page.get_by_label(label, exact=False)
                if loc.count() and loc.first.is_visible():
                    loc.first.fill(value)
                    self._track_field(field_name, filled=True)
                    return True
            except Exception:  # noqa: BLE001
                pass

            # 2. Placeholder
            try:
                loc = page.get_by_placeholder(label, exact=False)
                if loc.count() and loc.first.is_visible():
                    loc.first.fill(value)
                    self._track_field(field_name, filled=True)
                    return True
            except Exception:  # noqa: BLE001
                pass

            # 3. Attribute-based selectors
            for sel in (
                f'input[name="{label}"]',
                f'textarea[name="{label}"]',
                f'input[aria-label*="{label}" i]',
                f'textarea[aria-label*="{label}" i]',
                f'input[placeholder*="{label}" i]',
                f'textarea[placeholder*="{label}" i]',
            ):
                try:
                    loc = page.locator(sel).first
                    if loc.count() and loc.is_visible():
                        loc.fill(value)
                        self._track_field(field_name, filled=True)
                        return True
                except Exception:  # noqa: BLE001
                    pass

        self._track_field(field_name, filled=False)
        return False

    def fill_name(self, page: Page) -> None:
        """Fill first name, last name, and/or combined full-name fields."""
        p = self.profile
        if p.first_name:
            self.fill_text_field_by_label_or_placeholder(
                page, "first_name", p.first_name,
                "First name", "first name", "Imię", "imię", "firstname", "first_name",
            )
        if p.last_name:
            self.fill_text_field_by_label_or_placeholder(
                page, "last_name", p.last_name,
                "Last name", "last name", "Nazwisko", "nazwisko", "lastname", "surname",
            )
        if p.full_name:
            self.fill_text_field_by_label_or_placeholder(
                page, "full_name", p.full_name,
                "Full name", "full name", "Imię i nazwisko", "imię i nazwisko", "name",
            )

    def fill_email(self, page: Page) -> None:
        """Fill email address field."""
        if not self.profile.email:
            return
        self.fill_text_field_by_label_or_placeholder(
            page, "email", self.profile.email,
            "Email", "email", "E-mail", "e-mail", "email address", "adres e-mail",
        )

    def fill_phone(self, page: Page) -> None:
        """Fill phone number field."""
        if not self.profile.phone:
            return
        self.fill_text_field_by_label_or_placeholder(
            page, "phone", self.profile.phone,
            "Phone", "phone", "Telefon", "telefon", "mobile", "phone number",
            "numer telefonu", "Phone number",
        )

    def fill_linkedin(self, page: Page) -> None:
        """Fill LinkedIn profile URL field."""
        if not self.profile.linkedin:
            return
        self.fill_text_field_by_label_or_placeholder(
            page, "linkedin", self.profile.linkedin,
            "LinkedIn", "linkedin", "LinkedIn URL", "linkedin url", "LinkedIn profile",
        )

    def fill_github(self, page: Page) -> None:
        """Fill GitHub profile URL field."""
        if not self.profile.github:
            return
        self.fill_text_field_by_label_or_placeholder(
            page, "github", self.profile.github,
            "GitHub", "github", "GitHub URL", "github url",
        )

    def fill_portfolio(self, page: Page) -> None:
        """Fill portfolio / personal website URL field."""
        if not self.profile.portfolio:
            return
        self.fill_text_field_by_label_or_placeholder(
            page, "portfolio", self.profile.portfolio,
            "Portfolio", "portfolio", "Website", "website", "personal website",
            "Personal site", "strona", "www",
        )

    def fill_expected_salary(self, page: Page, contract: str = "") -> None:
        """Fill expected salary field based on contract type if configured."""
        contract_lower = contract.lower()
        salary: int | None = None
        if "b2b" in contract_lower and self.profile.expected_salary_b2b:
            salary = self.profile.expected_salary_b2b
        elif self.profile.expected_salary_uop:
            salary = self.profile.expected_salary_uop
        if salary is None:
            return
        self.fill_text_field_by_label_or_placeholder(
            page, "expected_salary", str(salary),
            "Expected salary", "expected salary", "Oczekiwane wynagrodzenie",
            "oczekiwane wynagrodzenie", "Salary", "salary", "wynagrodzenie", "stawka",
        )

    def fill_availability(self, page: Page) -> None:
        """Fill availability / notice period field."""
        value = self.profile.availability or self.profile.notice_period
        if not value:
            return
        self.fill_text_field_by_label_or_placeholder(
            page, "availability", value,
            "Availability", "availability", "Dostępność", "dostępność",
            "Notice period", "notice period", "okres wypowiedzenia",
            "Start date", "start date",
        )

    def upload_cv(self, page: Page) -> bool:
        """Upload the CV via a file input.  Returns True on success."""
        cv_path = self.profile.cv_path
        if not cv_path:
            return False
        try:
            file_input = page.locator('input[type="file"]').first
            if file_input.count():
                file_input.set_input_files(cv_path)
                self._track_field("cv_upload", filled=True)
                return True
        except Exception:  # noqa: BLE001
            pass
        self._track_field("cv_upload", filled=False)
        return False

    def handle_consents(self, page: Page) -> None:
        """Check required data-processing consents; skip marketing/newsletter.

        Rules:
        - Checkbox label matches required keywords (RODO/GDPR/przetwarzanie) → check.
        - Checkbox label matches marketing keywords → skip unless
          ``profile.consents.marketing`` is True.
        - Checkbox label contains future-recruitment patterns → skip unless
          ``profile.consents.future_recruitment`` is True.
        """
        if not self.profile.consents.data_processing:
            return

        try:
            checkboxes = page.locator('input[type="checkbox"]')
            count = checkboxes.count()
        except Exception:  # noqa: BLE001
            return

        for i in range(count):
            try:
                cb = checkboxes.nth(i)
                if not cb.is_visible() or cb.is_checked():
                    continue
                label_text = self._get_checkbox_label_text(page, cb).lower()
                if not label_text:
                    continue

                is_marketing = any(kw in label_text for kw in _MARKETING_KEYWORDS)
                is_future = "przyszł" in label_text or "future recruitment" in label_text
                is_required = any(kw in label_text for kw in _REQUIRED_CONSENT_KEYWORDS)

                if is_marketing and not self.profile.consents.marketing:
                    continue
                if is_future and not self.profile.consents.future_recruitment:
                    continue
                if is_required:
                    cb.check()
            except Exception:  # noqa: BLE001
                continue

    def collect_form_debug_info(self, page: Page) -> dict:
        """Collect non-sensitive form structure info for debugging."""
        try:
            return {
                "url": page.url,
                "title": page.title(),
                "inputs": page.locator("input:not([type=hidden])").count(),
                "textareas": page.locator("textarea").count(),
                "buttons": page.locator("button").count(),
                "checkboxes": page.locator('input[type="checkbox"]').count(),
                "file_inputs": page.locator('input[type="file"]').count(),
            }
        except Exception:  # noqa: BLE001
            return {}

    # ── Private helpers ───────────────────────────────────────────────────────

    def _track_field(self, field_name: str, *, filled: bool, required: bool = True) -> None:
        """Record a field as filled or missing in the active :class:`FillResult`."""
        if self._result is None:
            return
        if filled:
            if field_name not in self._result.fields_filled:
                self._result.fields_filled.append(field_name)
        elif required:
            # Only mark missing if not already filled (specific selector may have worked)
            if (
                field_name not in self._result.fields_missing
                and field_name not in self._result.fields_filled
            ):
                self._result.fields_missing.append(field_name)

    def _fill_by_selector(
        self,
        page: Page,
        selector: str,
        value: str,
        field_name: str,
    ) -> bool:
        """Fill an element by exact CSS selector.

        Tracks a success in *_result* but does NOT track failure — a missing
        portal-specific element is expected to fall through to generic helpers.
        """
        if not value:
            return False
        try:
            el = page.locator(selector).first
            if el.count() and el.is_visible():
                el.fill(value)
                self._track_field(field_name, filled=True)
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _try_fill_field(self, page: object, label_or_name: str, value: str) -> bool:
        """Low-level legacy helper.  Does not update *_result* tracking.

        Prefer :meth:`fill_text_field_by_label_or_placeholder` for new code.
        """
        assert isinstance(page, Page)
        for sel in (
            f'[name="{label_or_name}"]',
            f'[placeholder*="{label_or_name}" i]',
            f'input[aria-label*="{label_or_name}" i]',
            f'textarea[aria-label*="{label_or_name}" i]',
        ):
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.fill(value)
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _try_upload_cv(self, page: object) -> bool:
        """Legacy alias for :meth:`upload_cv`.  Kept for backward compatibility."""
        assert isinstance(page, Page)
        return self.upload_cv(page)

    def _check_login_required(self, page: Page) -> bool:
        """Return True if the current page appears to require a login."""
        try:
            url = page.url.lower()
            if any(
                k in url
                for k in ("/login", "/signin", "/sign-in", "/logowanie", "/zaloguj", "/auth/")
            ):
                return True
            if page.locator('input[type="password"]').first.is_visible():
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _get_checkbox_label_text(self, page: Page, checkbox: Locator) -> str:
        """Return the label text for *checkbox* via multiple discovery strategies."""
        # 1. aria-label attribute
        try:
            aria_label = checkbox.get_attribute("aria-label") or ""
            if aria_label:
                return aria_label
        except Exception:  # noqa: BLE001
            pass

        # 2. <label for="id">
        try:
            cb_id = checkbox.get_attribute("id") or ""
            if cb_id:
                label = page.locator(f'label[for="{cb_id}"]')
                if label.count():
                    return label.first.inner_text(timeout=1_000)
        except Exception:  # noqa: BLE001
            pass

        # 3. aria-describedby
        try:
            described_by = checkbox.get_attribute("aria-describedby") or ""
            if described_by:
                desc = page.locator(f"#{described_by}")
                if desc.count():
                    return desc.first.inner_text(timeout=1_000)
        except Exception:  # noqa: BLE001
            pass

        # 4. Text in parent element
        try:
            return checkbox.locator("xpath=..").inner_text(timeout=1_000)
        except Exception:  # noqa: BLE001
            pass

        return ""

    def _save_debug_screenshot(self, page: Page, offer_id: str) -> None:
        """Save a screenshot to ``data/debug/screenshots/`` for debug mode."""
        try:
            debug_dir = Path("data/debug/screenshots")
            debug_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = debug_dir / f"{offer_id[:8]}_{ts}.png"
            page.screenshot(path=str(filename))
            if self._result is not None:
                self._result.warnings.append(f"Debug screenshot saved: {filename}")
        except Exception:  # noqa: BLE001
            pass

    def _finalize_status(self) -> None:
        """Determine final FillStatus from fields_filled / fields_missing."""
        result = self._result
        if result is None or result.error:
            return
        if not result.fields_filled:
            result.status = FillStatus.PARTIAL
            if not result.warnings:
                result.warnings.append("No form fields were filled successfully.")
        elif result.fields_missing:
            result.status = FillStatus.PARTIAL
        else:
            result.status = FillStatus.FILLED

    def _print_review_prompt(self) -> None:
        print(
            "\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "  Application form has been filled.\n"
            "  Please review it manually before submitting.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )
