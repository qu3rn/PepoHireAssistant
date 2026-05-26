"""Tests for source-specific form fillers, FillResult model, and service layer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cv_sender.config import Consents, FormFillingConfig, Profile, Settings
from cv_sender.models import (
    Application,
    ApplicationStatus,
    Decision,
    FillResult,
    FillStatus,
    Offer,
)
from cv_sender.portals.base import _MARKETING_KEYWORDS, _REQUIRED_CONSENT_KEYWORDS
from cv_sender.portals.generic import GenericFiller
from cv_sender.portals.justjoin import JustJoinFiller
from cv_sender.portals.nofluffjobs import NoFluffJobsFiller
from cv_sender.portals.pracuj import PracujFiller
from cv_sender.portals.rocketjobs import RocketJobsFiller


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile(**kwargs) -> Profile:
    defaults = dict(
        first_name="Jan",
        last_name="Kowalski",
        email="jan@example.com",
        phone="500100200",
        linkedin="https://linkedin.com/in/jan",
        github="https://github.com/jan",
        cv_path="",
        expected_salary_b2b=20000,
        expected_salary_uop=14000,
        consents=Consents(data_processing=True),
    )
    defaults.update(kwargs)
    return Profile(**defaults)


def _make_settings(**kwargs) -> Settings:
    return Settings(**kwargs)


def _make_offer(**kwargs) -> Offer:
    defaults = dict(url="https://rocketjobs.pl/job/123", title="Backend Dev", company="ACME")
    defaults.update(kwargs)
    return Offer(**defaults)


@pytest.fixture(autouse=True)
def patch_storage_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_OFFERS", tmp_path / "offers.json")
    monkeypatch.setattr(_storage, "_DEFAULT_APPLICATIONS", tmp_path / "applications.json")


# ---------------------------------------------------------------------------
# FillResult model
# ---------------------------------------------------------------------------


class TestFillResult:
    def test_default_empty_lists(self) -> None:
        r = FillResult(status=FillStatus.FAILED, offer_id="x", url="https://x.com")
        assert r.fields_filled == []
        assert r.fields_missing == []
        assert r.warnings == []
        assert r.error is None

    def test_status_is_string_comparable(self) -> None:
        r = FillResult(status=FillStatus.FILLED, offer_id="x", url="https://x.com")
        assert r.status == "filled"
        assert r.status == FillStatus.FILLED

    def test_partial_status(self) -> None:
        r = FillResult(
            status=FillStatus.PARTIAL,
            offer_id="x",
            url="https://x.com",
            fields_filled=["email"],
            fields_missing=["phone"],
        )
        assert r.status == FillStatus.PARTIAL
        assert "email" in r.fields_filled
        assert "phone" in r.fields_missing


# ---------------------------------------------------------------------------
# Filler selection by source
# ---------------------------------------------------------------------------


class TestFillerSelection:
    def _choose(self, url: str) -> object:
        from cv_sender.form_filler import _choose_filler

        profile = _make_profile()
        settings = _make_settings()
        return _choose_filler(url, profile, settings)

    def test_rocketjobs_selected(self) -> None:
        filler = self._choose("https://rocketjobs.pl/job/123")
        assert isinstance(filler, RocketJobsFiller)

    def test_rocketjobs_www_selected(self) -> None:
        filler = self._choose("https://www.rocketjobs.pl/job/123")
        assert isinstance(filler, RocketJobsFiller)

    def test_justjoin_selected(self) -> None:
        filler = self._choose("https://justjoin.it/offers/job-123")
        assert isinstance(filler, JustJoinFiller)

    def test_nofluffjobs_selected(self) -> None:
        filler = self._choose("https://nofluffjobs.com/job/job-123")
        assert isinstance(filler, NoFluffJobsFiller)

    def test_pracuj_selected(self) -> None:
        filler = self._choose("https://pracuj.pl/praca/backend-developer,123456")
        assert isinstance(filler, PracujFiller)

    def test_generic_for_unknown_host(self) -> None:
        filler = self._choose("https://example.com/job/123")
        assert isinstance(filler, GenericFiller)

    def test_generic_for_evil_linkedin_url(self) -> None:
        """Subdomain/path spoofing must not match a different portal."""
        filler = self._choose("https://evil.com/linkedin.com")
        assert isinstance(filler, GenericFiller)


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------


class TestCanHandle:
    def _offer(self, url: str) -> Offer:
        return Offer(url=url, title="Dev", source="test")

    def test_generic_handles_anything(self) -> None:
        profile = _make_profile()
        settings = _make_settings()
        filler = GenericFiller(profile=profile, settings=settings)
        assert filler.can_handle(self._offer("https://anything.com"))

    def test_rocketjobs_handles_anything(self) -> None:
        # can_handle defaults to True on all fillers
        profile = _make_profile()
        settings = _make_settings()
        filler = RocketJobsFiller(profile=profile, settings=settings)
        assert filler.can_handle(self._offer("https://rocketjobs.pl/job/1"))


# ---------------------------------------------------------------------------
# _track_field logic
# ---------------------------------------------------------------------------


class TestTrackField:
    def _filler(self) -> GenericFiller:
        return GenericFiller(profile=_make_profile(), settings=_make_settings())

    def test_filled_adds_to_fields_filled(self) -> None:
        f = self._filler()
        result = FillResult(status=FillStatus.FAILED, offer_id="x", url="y")
        f._result = result
        f._track_field("email", filled=True)
        assert "email" in result.fields_filled
        assert "email" not in result.fields_missing

    def test_not_filled_adds_to_fields_missing(self) -> None:
        f = self._filler()
        result = FillResult(status=FillStatus.FAILED, offer_id="x", url="y")
        f._result = result
        f._track_field("phone", filled=False)
        assert "phone" in result.fields_missing
        assert "phone" not in result.fields_filled

    def test_no_duplicate_in_fields_filled(self) -> None:
        f = self._filler()
        result = FillResult(status=FillStatus.FAILED, offer_id="x", url="y")
        f._result = result
        f._track_field("email", filled=True)
        f._track_field("email", filled=True)
        assert result.fields_filled.count("email") == 1

    def test_not_added_to_missing_if_already_filled(self) -> None:
        """Portal-specific selector fills field; generic fallback must not re-mark missing."""
        f = self._filler()
        result = FillResult(status=FillStatus.FAILED, offer_id="x", url="y")
        f._result = result
        f._track_field("first_name", filled=True)
        f._track_field("first_name", filled=False)  # generic fallback didn't find field
        assert "first_name" not in result.fields_missing

    def test_no_tracking_when_result_is_none(self) -> None:
        f = self._filler()
        f._result = None
        # Must not raise
        f._track_field("email", filled=True)
        f._track_field("phone", filled=False)


# ---------------------------------------------------------------------------
# _finalize_status
# ---------------------------------------------------------------------------


class TestFinalizeStatus:
    def _result(self, **kwargs) -> FillResult:
        return FillResult(status=FillStatus.FAILED, offer_id="x", url="y", **kwargs)

    def _filler(self) -> GenericFiller:
        return GenericFiller(profile=_make_profile(), settings=_make_settings())

    def test_filled_when_all_fields_present(self) -> None:
        f = self._filler()
        f._result = self._result(fields_filled=["email", "phone"])
        f._finalize_status()
        assert f._result.status == FillStatus.FILLED

    def test_partial_when_fields_missing(self) -> None:
        f = self._filler()
        f._result = self._result(fields_filled=["email"], fields_missing=["phone"])
        f._finalize_status()
        assert f._result.status == FillStatus.PARTIAL

    def test_partial_when_nothing_filled(self) -> None:
        f = self._filler()
        f._result = self._result()
        f._finalize_status()
        assert f._result.status == FillStatus.PARTIAL
        assert f._result.warnings  # default warning added

    def test_keeps_failed_on_error(self) -> None:
        f = self._filler()
        f._result = self._result(error="crash")
        f._finalize_status()
        assert f._result.status == FillStatus.FAILED

    def test_no_default_warning_if_custom_warning_present(self) -> None:
        f = self._filler()
        f._result = self._result(warnings=["Login required."])
        f._finalize_status()
        # Should not add a second warning on top of the custom one
        assert f._result.warnings.count("No form fields were filled successfully.") == 0


# ---------------------------------------------------------------------------
# Consent keyword lists
# ---------------------------------------------------------------------------


class TestConsentKeywords:
    def test_required_keywords_present(self) -> None:
        assert "rodo" in _REQUIRED_CONSENT_KEYWORDS
        assert "gdpr" in _REQUIRED_CONSENT_KEYWORDS
        assert "przetwarzanie" in _REQUIRED_CONSENT_KEYWORDS
        assert "privacy" in _REQUIRED_CONSENT_KEYWORDS

    def test_marketing_keywords_present(self) -> None:
        assert "marketing" in _MARKETING_KEYWORDS
        assert "newsletter" in _MARKETING_KEYWORDS
        assert "future recruitment" in _MARKETING_KEYWORDS


# ---------------------------------------------------------------------------
# handle_consents – no-op when data_processing is False
# ---------------------------------------------------------------------------


class TestHandleConsents:
    def test_no_interaction_without_data_processing_consent(self) -> None:
        profile = _make_profile(consents=Consents(data_processing=False))
        filler = GenericFiller(profile=profile, settings=_make_settings())
        page = MagicMock()
        filler.handle_consents(page)
        page.locator.assert_not_called()

    def test_no_interaction_when_count_is_zero(self) -> None:
        profile = _make_profile(consents=Consents(data_processing=True))
        filler = GenericFiller(profile=profile, settings=_make_settings())

        page = MagicMock()
        mock_checkboxes = MagicMock()
        mock_checkboxes.count.return_value = 0
        page.locator.return_value = mock_checkboxes

        filler.handle_consents(page)
        mock_checkboxes.count.assert_called_once()


# ---------------------------------------------------------------------------
# fill_application_form service
# ---------------------------------------------------------------------------


class TestFillApplicationFormService:
    @pytest.fixture(autouse=True)
    def _add_offer(self, tmp_path: Path) -> None:
        from cv_sender.storage import add_offer

        self.offer = _make_offer()
        add_offer(self.offer)

    def test_returns_failed_for_missing_offer(self) -> None:
        from cv_sender.services import fill_application_form

        result = fill_application_form("nonexistent-offer-id")
        assert result.status == FillStatus.FAILED
        assert "not found" in (result.error or "")

    def test_creates_application_record_on_success(self) -> None:
        from cv_sender.services import fill_application_form
        from cv_sender.storage import load_applications

        filled_result = FillResult(
            status=FillStatus.FILLED,
            offer_id=self.offer.id,
            url=self.offer.url,
            fields_filled=["email", "phone"],
        )
        with patch("cv_sender.form_filler.fill_application_with_result", return_value=filled_result):
            result = fill_application_form(self.offer.id)

        assert result.status == FillStatus.FILLED
        apps = load_applications()
        assert any(a.offer_id == self.offer.id for a in apps)

    def test_creates_failed_application_record_on_failure(self) -> None:
        from cv_sender.services import fill_application_form
        from cv_sender.storage import load_applications

        failed_result = FillResult(
            status=FillStatus.FAILED,
            offer_id=self.offer.id,
            url=self.offer.url,
            error="Browser crashed",
        )
        with patch("cv_sender.form_filler.fill_application_with_result", return_value=failed_result):
            result = fill_application_form(self.offer.id)

        assert result.status == FillStatus.FAILED
        apps = load_applications()
        assert any(
            a.offer_id == self.offer.id and a.status == ApplicationStatus.FAILED for a in apps
        )

    def test_appends_form_filled_event(self) -> None:
        from cv_sender.services import fill_application_form
        from cv_sender.storage import load_applications

        ok_result = FillResult(
            status=FillStatus.PARTIAL,
            offer_id=self.offer.id,
            url=self.offer.url,
            fields_filled=["email"],
            fields_missing=["phone"],
        )
        with patch("cv_sender.form_filler.fill_application_with_result", return_value=ok_result):
            fill_application_form(self.offer.id)

        apps = load_applications()
        app = next(a for a in apps if a.offer_id == self.offer.id)
        events = [e.event for e in app.events]
        assert "form_filled" in events

    def test_exception_in_filler_returns_failed_result(self) -> None:
        from cv_sender.services import fill_application_form

        with patch(
            "cv_sender.form_filler.fill_application_with_result",
            side_effect=RuntimeError("Playwright failed"),
        ):
            result = fill_application_form(self.offer.id)

        assert result.status == FillStatus.FAILED
        assert "Playwright failed" in (result.error or "")

    def test_exception_does_not_crash_service(self) -> None:
        """Unhandled exceptions are caught and returned as FillResult, never re-raised."""
        from cv_sender.services import fill_application_form

        with patch(
            "cv_sender.form_filler.fill_application_with_result",
            side_effect=Exception("Unknown error"),
        ):
            # Must not raise
            result = fill_application_form(self.offer.id)
        assert result.status == FillStatus.FAILED


# ---------------------------------------------------------------------------
# fill_application_with_result – fallback to GenericFiller
# ---------------------------------------------------------------------------


class TestFillApplicationWithResultFallback:
    def test_falls_back_to_generic_on_specific_filler_failure(self) -> None:
        from cv_sender.form_filler import fill_application_with_result

        offer = _make_offer(url="https://rocketjobs.pl/job/1")
        profile = _make_profile()
        settings = _make_settings()

        failed_specific = FillResult(
            status=FillStatus.FAILED,
            offer_id=offer.id,
            url=offer.url,
            error="Browser error",
        )
        generic_result = FillResult(
            status=FillStatus.PARTIAL,
            offer_id=offer.id,
            url=offer.url,
            fields_filled=["email"],
        )

        with (
            patch.object(RocketJobsFiller, "fill", return_value=failed_specific),
            patch.object(GenericFiller, "fill", return_value=generic_result) as mock_generic,
        ):
            result = fill_application_with_result(offer, profile, settings)

        mock_generic.assert_called_once()
        assert result.status == FillStatus.PARTIAL

    def test_no_fallback_if_specific_filler_succeeds(self) -> None:
        from cv_sender.form_filler import fill_application_with_result

        offer = _make_offer(url="https://justjoin.it/offers/job-1")
        profile = _make_profile()
        settings = _make_settings()

        ok_result = FillResult(
            status=FillStatus.FILLED,
            offer_id=offer.id,
            url=offer.url,
            fields_filled=["email", "phone"],
        )

        with (
            patch.object(JustJoinFiller, "fill", return_value=ok_result),
            patch.object(GenericFiller, "fill") as mock_generic,
        ):
            result = fill_application_with_result(offer, profile, settings)

        mock_generic.assert_not_called()
        assert result.status == FillStatus.FILLED

    def test_generic_filler_used_directly_for_unknown_host(self) -> None:
        from cv_sender.form_filler import fill_application_with_result

        offer = _make_offer(url="https://example.com/job/1")
        profile = _make_profile()
        settings = _make_settings()

        generic_result = FillResult(
            status=FillStatus.PARTIAL,
            offer_id=offer.id,
            url=offer.url,
        )

        with patch.object(GenericFiller, "fill", return_value=generic_result) as mock_generic:
            result = fill_application_with_result(offer, profile, settings)

        mock_generic.assert_called_once()
        assert result == generic_result
