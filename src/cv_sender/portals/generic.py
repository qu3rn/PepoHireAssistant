"""Generic form filler – works as a fallback for any job portal."""

from __future__ import annotations

from playwright.sync_api import Page

from cv_sender.portals.base import BasePortalFiller


class GenericFiller(BasePortalFiller):
    """Best-effort form filler for unknown portals.

    Uses the rich base-class helpers to fill common Polish/English labels.
    Kept as the registered fallback when no source-specific filler matches.
    """

    source = "generic"

    def fill_form(self, page: Page) -> None:  # type: ignore[override]
        """Try to fill common application form fields generically."""
        self.click_apply_button(page)
        self.wait_for_form_ready(page)
        self.fill_name(page)
        self.fill_email(page)
        self.fill_phone(page)
        self.fill_linkedin(page)
        self.fill_github(page)
        self.fill_portfolio(page)
        self.fill_expected_salary(page)
        self.fill_availability(page)
        self.fill_textarea_questions(page)
        self.upload_cv(page)
        self.handle_consents(page)

    # ── Legacy aliases (LinkedInFiller and other callers depend on these) ──────

    def _click_apply_button(self, page: Page) -> None:
        """Backward-compatible alias for :meth:`click_apply_button`."""
        self.click_apply_button(page)

    def _fill_profile_fields(self, page: Page) -> None:
        """Backward-compatible alias: fill common profile fields generically."""
        self.fill_name(page)
        self.fill_email(page)
        self.fill_phone(page)
        self.fill_linkedin(page)
        self.fill_github(page)
        self.fill_portfolio(page)

    def _check_data_processing_consent(self, page: Page) -> None:
        """Backward-compatible alias for :meth:`handle_consents`."""
        self.handle_consents(page)
