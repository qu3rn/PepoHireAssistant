"""Pracuj.pl form filler."""

from __future__ import annotations

from playwright.sync_api import Page

from cv_sender.portals.generic import GenericFiller


class PracujFiller(GenericFiller):
    """Form filler for pracuj.pl.

    Extends :class:`GenericFiller` with Pracuj-specific selectors.
    Falls back to generic behaviour for unknown fields.
    """

    def fill_form(self, page: Page) -> None:  # type: ignore[override]
        self._click_apply_button(page)
        self._fill_pracuj_fields(page)
        self._try_upload_cv(page)
        self._check_data_processing_consent(page)

    def _fill_pracuj_fields(self, page: Page) -> None:
        """Fill fields using Pracuj-specific selectors, then fall back to generic."""
        profile = self.profile

        pracuj_fields = {
            'input[data-test="input-name"]': profile.first_name,
            'input[data-test="input-surname"]': profile.last_name,
            'input[data-test="input-email"]': profile.email,
            'input[data-test="input-phone"]': profile.phone,
        }

        for selector, value in pracuj_fields.items():
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
