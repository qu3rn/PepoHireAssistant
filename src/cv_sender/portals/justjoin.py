"""JustJoinIT form filler."""

from __future__ import annotations

from playwright.sync_api import Page

from cv_sender.portals.base import BasePortalFiller


class JustJoinFiller(BasePortalFiller):
    """Form filler for justjoin.it.

    JustJoinIT uses a React-based SPA.  The filler tries known input name
    attributes first, then falls back to generic label helpers.

    If a login wall is detected (some offers redirect to the JustJoinIT
    account portal), the filler returns a PARTIAL result with a warning.
    """

    source = "justjoin.it"

    def fill_form(self, page: Page) -> None:  # type: ignore[override]
        self.click_apply_button(page)
        self.wait_for_form_ready(page)

        if self._check_login_required(page):
            if self._result is not None:
                self._result.warnings.append(
                    "JustJoinIT may require login to apply. "
                    "Please log in manually, then fill and submit the form."
                )
            return

        p = self.profile

        # JustJoinIT-specific input name attributes
        self._fill_by_selector(page, 'input[name="first_name"]', p.first_name, "first_name")
        self._fill_by_selector(page, 'input[name="last_name"]', p.last_name, "last_name")
        self._fill_by_selector(page, 'input[name="email"]', p.email, "email")
        self._fill_by_selector(page, 'input[name="phone"]', p.phone, "phone")
        self._fill_by_selector(page, 'input[name="linkedin_url"]', p.linkedin, "linkedin")
        self._fill_by_selector(page, 'input[name="github_url"]', p.github, "github")

        # Generic fallback for anything not found via specific selectors
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
