"""LinkedIn form filler."""

from __future__ import annotations

from playwright.sync_api import Page

from cv_sender.portals.generic import GenericFiller


class LinkedInFiller(GenericFiller):
    """Form filler for LinkedIn Easy Apply.

    LinkedIn's Easy Apply flow consists of multi-step modal dialogs.
    This filler attempts a best-effort fill of each step.
    It does NOT bypass login or bot-protection measures.
    """

    def fill_form(self, page: Page) -> None:  # type: ignore[override]
        self._click_easy_apply(page)
        self._fill_linkedin_fields(page)
        self._try_upload_cv(page)
        # Do not click the final "Submit application" button

    def _click_easy_apply(self, page: Page) -> None:
        """Click the LinkedIn 'Easy Apply' button."""
        try:
            btn = page.get_by_role("button", name="Easy Apply")
            if btn.count() and btn.first.is_visible():
                btn.first.click()
                page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:  # noqa: BLE001
            # Fall back to generic apply button
            self._click_apply_button(page)

    def _fill_linkedin_fields(self, page: Page) -> None:
        """Fill LinkedIn-specific fields."""
        profile = self.profile

        linkedin_fields = {
            'input[id*="phoneNumber"]': profile.phone,
            'input[id*="email"]': profile.email,
            'input[id*="city"]': profile.city,
        }

        for selector, value in linkedin_fields.items():
            if not value:
                continue
            try:
                el = page.locator(selector).first
                if el.count() and el.is_visible():
                    el.fill(value)
            except Exception:  # noqa: BLE001
                pass

        # Generic fallback
        self._fill_profile_fields(page)
