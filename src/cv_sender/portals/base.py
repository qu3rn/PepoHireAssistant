"""Abstract base class for portal-specific form fillers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from cv_sender.browser import browser_session, navigate
from cv_sender.config import Profile, Settings


class BasePortalFiller(ABC):
    """Common interface for all portal fillers.

    Subclasses implement :meth:`fill_form` which receives the already-loaded
    Playwright *page* and should fill the application form without submitting.
    """

    def __init__(self, profile: Profile, settings: Settings, headless: bool = False) -> None:
        self.profile = profile
        self.settings = settings
        self.headless = headless

    def run(self, url: str, *, wait_for_review: bool = True) -> None:
        """Open *url* in a browser, fill the form, then pause for manual review.

        When *wait_for_review* is ``False`` the blocking ``input()`` prompt is
        skipped so the method can be called safely from non-interactive
        contexts such as the Streamlit UI.  The form is still **never**
        auto-submitted regardless of this flag.
        """
        with browser_session(headless=self.headless) as (page, _browser):
            navigate(page, url)
            self.fill_form(page)
            self._print_review_prompt()
            if wait_for_review:
                input("Press ENTER when you are ready to close the browser…")

    @abstractmethod
    def fill_form(self, page: object) -> None:  # page: playwright Page
        """Fill the application form on the already-loaded *page*."""

    # ------------------------------------------------------------------
    # Helpers shared by all fillers
    # ------------------------------------------------------------------

    def _print_review_prompt(self) -> None:
        print(
            "\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "  Application form has been filled.\n"
            "  Please review it manually before submitting.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )

    def _try_fill_field(self, page: object, label_or_name: str, value: str) -> bool:
        """Try to fill a field identified by label, name, or placeholder.

        Returns ``True`` if a field was found and filled.
        """
        from playwright.sync_api import Page  # noqa: PLC0415

        assert isinstance(page, Page)
        selectors = [
            f'[name="{label_or_name}"]',
            f'[placeholder*="{label_or_name}" i]',
            f'input[aria-label*="{label_or_name}" i]',
            f'textarea[aria-label*="{label_or_name}" i]',
        ]
        for sel in selectors:
            try:
                locator = page.locator(sel).first
                if locator.count() and locator.is_visible():
                    locator.fill(value)
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _try_upload_cv(self, page: object) -> bool:
        """Try to upload the CV file via ``input[type=file]``."""
        from playwright.sync_api import Page  # noqa: PLC0415

        assert isinstance(page, Page)
        cv_path = self.profile.cv_path
        if not cv_path:
            return False
        try:
            file_input = page.locator('input[type="file"]').first
            if file_input.count():
                file_input.set_input_files(cv_path)
                return True
        except Exception:  # noqa: BLE001
            pass
        return False
