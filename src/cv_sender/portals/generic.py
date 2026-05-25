"""Generic form filler – works as a fallback for any job portal."""

from __future__ import annotations

from playwright.sync_api import Page

from cv_sender.portals.base import BasePortalFiller

# Buttons that typically lead to the application form
_APPLY_BUTTON_TEXTS = [
    "Apply",
    "Apply now",
    "Apply for this job",
    "Aplikuj",
    "Aplikuj teraz",
    "Wyślij CV",
    "Aplikuj na to stanowisko",
]

# Common field name/label mappings
_FIELD_MAP = [
    ("first_name", ["first name", "imię", "name", "firstname"]),
    ("last_name", ["last name", "nazwisko", "surname", "lastname"]),
    ("email", ["email", "e-mail", "email address"]),
    ("phone", ["phone", "telefon", "mobile", "phone number"]),
    ("city", ["city", "miasto", "location"]),
    ("linkedin", ["linkedin", "linkedin url", "linkedin profile"]),
    ("portfolio", ["portfolio", "website", "personal website"]),
]


class GenericFiller(BasePortalFiller):
    """Best-effort form filler for unknown portals."""

    def fill_form(self, page: Page) -> None:  # type: ignore[override]
        """Try to fill common application form fields generically."""
        self._click_apply_button(page)
        self._fill_profile_fields(page)
        self._try_upload_cv(page)
        self._check_data_processing_consent(page)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _click_apply_button(self, page: Page) -> None:
        """Try to click an 'Apply' button to open the application form."""
        for text in _APPLY_BUTTON_TEXTS:
            try:
                btn = page.get_by_role("button", name=text)
                if btn.count() and btn.first.is_visible():
                    btn.first.click()
                    page.wait_for_load_state("networkidle", timeout=10_000)
                    return
            except Exception:  # noqa: BLE001
                pass

            try:
                link = page.get_by_role("link", name=text)
                if link.count() and link.first.is_visible():
                    link.first.click()
                    page.wait_for_load_state("networkidle", timeout=10_000)
                    return
            except Exception:  # noqa: BLE001
                pass

    def _fill_profile_fields(self, page: Page) -> None:
        """Map profile attributes to form fields."""
        for attr, labels in _FIELD_MAP:
            value = getattr(self.profile, attr, "")
            if not value:
                continue
            for label in labels:
                if self._try_fill_field(page, label, value):
                    break

        # Full name as a combined field
        full_name = self.profile.full_name
        if full_name:
            self._try_fill_field(page, "full name", full_name)
            self._try_fill_field(page, "imię i nazwisko", full_name)

    def _check_data_processing_consent(self, page: Page) -> None:
        """Check data-processing consent checkbox only (never marketing)."""
        if not self.profile.consents.data_processing:
            return

        consent_patterns = [
            'input[type="checkbox"][name*="process" i]',
            'input[type="checkbox"][name*="consent" i]',
            'input[type="checkbox"][name*="rodo" i]',
            'input[type="checkbox"][id*="process" i]',
            'input[type="checkbox"][id*="consent" i]',
            'input[type="checkbox"][id*="rodo" i]',
        ]
        for selector in consent_patterns:
            try:
                checkbox = page.locator(selector).first
                if checkbox.count() and checkbox.is_visible() and not checkbox.is_checked():
                    checkbox.check()
                    return  # Only check the first matching consent
            except Exception:  # noqa: BLE001
                continue
