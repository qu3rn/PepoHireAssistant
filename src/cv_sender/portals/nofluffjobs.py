"""NoFluffJobs form filler."""

from __future__ import annotations

from playwright.sync_api import Page

from cv_sender.portals.base import BasePortalFiller


class NoFluffJobsFiller(BasePortalFiller):
    """Form filler for nofluffjobs.com.

    NoFluffJobs typically requires an account to apply.  If a login wall
    or registration gate is detected, the filler returns a PARTIAL result
    with a clear warning rather than attempting to bypass authentication.
    """

    source = "nofluffjobs.com"

    def fill_form(self, page: Page) -> None:  # type: ignore[override]
        self.click_apply_button(page)
        self.wait_for_form_ready(page)

        if self._check_login_required(page):
            if self._result is not None:
                self._result.warnings.append(
                    "NoFluffJobs requires an account to apply. "
                    "Please log in or register, then fill and submit the form manually."
                )
            return

        p = self.profile

        # NoFluffJobs-specific input name attributes
        self._fill_by_selector(page, 'input[name="email"]', p.email, "email")
        self._fill_by_selector(page, 'input[name="phone"]', p.phone, "phone")
        self._fill_by_selector(
            page, 'input[name="salary_expectation"]', str(p.expected_salary_b2b or ""),
            "expected_salary",
        )

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
