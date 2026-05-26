"""Pracuj.pl form filler."""

from __future__ import annotations

from playwright.sync_api import Page

from cv_sender.portals.base import BasePortalFiller


class PracujFiller(BasePortalFiller):
    """Form filler for pracuj.pl.

    Pracuj.pl requires an account to apply.  If a login wall is detected,
    the filler returns a PARTIAL result with an explanatory warning instead
    of attempting to bypass the authentication.
    """

    source = "pracuj.pl"

    def fill_form(self, page: Page) -> None:  # type: ignore[override]
        self.click_apply_button(page)
        self.wait_for_form_ready(page)

        if self._check_login_required(page):
            if self._result is not None:
                self._result.warnings.append(
                    "Pracuj.pl requires login to apply. "
                    "Please log in manually, then fill and submit the form."
                )
            return

        p = self.profile

        # Pracuj-specific data-test attribute selectors
        self._fill_by_selector(page, 'input[data-test="input-name"]', p.first_name, "first_name")
        self._fill_by_selector(page, 'input[data-test="input-surname"]', p.last_name, "last_name")
        self._fill_by_selector(page, 'input[data-test="input-email"]', p.email, "email")
        self._fill_by_selector(page, 'input[data-test="input-phone"]', p.phone, "phone")

        # Generic fallback
        self.fill_name(page)
        self.fill_email(page)
        self.fill_phone(page)
        self.fill_linkedin(page)
        self.fill_github(page)
        self.fill_portfolio(page)
        self.fill_expected_salary(page)
        self.fill_availability(page)

        self.upload_cv(page)
        self.handle_consents(page)
