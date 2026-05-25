"""RocketJobs.pl form filler."""

from __future__ import annotations

from playwright.sync_api import Page

from cv_sender.portals.generic import GenericFiller


class RocketJobsFiller(GenericFiller):
    """Form filler for rocketjobs.pl.

    Extends :class:`GenericFiller` with RocketJobs-specific selectors.
    Falls back to generic behaviour for unknown fields.
    """

    def fill_form(self, page: Page) -> None:  # type: ignore[override]
        self._click_apply_button(page)
        self._fill_rocketjobs_fields(page)
        self._try_upload_cv(page)
        self._check_data_processing_consent(page)

    def _fill_rocketjobs_fields(self, page: Page) -> None:
        """Fill fields using RocketJobs-specific selectors, then fall back to generic."""
        profile = self.profile

        rocketjobs_fields = {
            'input[name="firstName"]': profile.first_name,
            'input[name="lastName"]': profile.last_name,
            'input[name="email"]': profile.email,
            'input[name="phone"]': profile.phone,
            'input[name="linkedin"]': profile.linkedin,
        }

        for selector, value in rocketjobs_fields.items():
            if not value:
                continue
            try:
                el = page.locator(selector).first
                if el.count() and el.is_visible():
                    el.fill(value)
            except Exception:  # noqa: BLE001
                pass

        # Fall back to generic field filling for anything not covered above
        self._fill_profile_fields(page)
