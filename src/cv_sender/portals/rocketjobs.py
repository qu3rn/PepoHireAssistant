"""RocketJobs.pl form filler."""

from __future__ import annotations

from playwright.sync_api import Page

from cv_sender.portals.base import BasePortalFiller


class RocketJobsFiller(BasePortalFiller):
    """Form filler for rocketjobs.pl.

    Tries portal-specific input selectors first, then falls back to the
    generic label/placeholder helpers for anything not covered.
    """

    source = "rocketjobs.pl"

    def fill_form(self, page: Page) -> None:  # type: ignore[override]
        self.click_apply_button(page)
        self.wait_for_form_ready(page)

        p = self.profile

        # Portal-specific field selectors (these are the known RocketJobs names)
        self._fill_by_selector(page, 'input[name="firstName"]', p.first_name, "first_name")
        self._fill_by_selector(page, 'input[name="lastName"]', p.last_name, "last_name")
        self._fill_by_selector(page, 'input[name="email"]', p.email, "email")
        self._fill_by_selector(page, 'input[name="phone"]', p.phone, "phone")
        self._fill_by_selector(page, 'input[name="linkedin"]', p.linkedin, "linkedin")
        self._fill_by_selector(page, 'input[name="github"]', p.github, "github")

        # Generic fallback for any fields not yet filled via specific selectors
        self.fill_name(page)
        self.fill_email(page)
        self.fill_phone(page)
        self.fill_linkedin(page)
        self.fill_github(page)
        self.fill_portfolio(page)

        # Salary / availability
        self.fill_expected_salary(page, contract=p.contract if hasattr(p, "contract") else "")
        self.fill_availability(page)

        self.upload_cv(page)
        self.handle_consents(page)
