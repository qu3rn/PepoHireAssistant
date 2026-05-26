"""Tests for form-filling debug panel: step logger, snapshots, detection, service, retry."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cv_sender.config import Consents, FormFillingConfig, Profile, Settings
from cv_sender.form_debug import (
    FormFillDebugRecord,
    StepLogger,
    _SENSITIVE_FIELDS,
    detect_blocked_page,
    detect_captcha,
    detect_login_wall,
    load_debug_run,
    load_debug_runs,
    load_form_snapshot,
    load_step_log,
    save_debug_run,
    snapshot_form,
)
from cv_sender.models import FillResult, FillStatus, Offer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile(**kwargs) -> Profile:
    defaults = dict(
        first_name="Jan",
        last_name="Kowalski",
        email="jan@example.com",
        phone="500100200",
        consents=Consents(data_processing=True),
    )
    defaults.update(kwargs)
    return Profile(**defaults)


def _make_settings(**kwargs) -> Settings:
    return Settings(**kwargs)


def _make_offer(**kwargs) -> Offer:
    defaults = dict(url="https://rocketjobs.pl/job/123", title="Dev", company="ACME")
    defaults.update(kwargs)
    return Offer(**defaults)


@pytest.fixture(autouse=True)
def patch_debug_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect debug artifact writes to a temp directory."""
    import cv_sender.form_debug as _fd

    monkeypatch.setattr(_fd, "_DEBUG_BASE", tmp_path / "debug")


# ---------------------------------------------------------------------------
# StepLogger — no sensitive values stored
# ---------------------------------------------------------------------------


class TestStepLogger:
    def test_log_fields_recorded(self) -> None:
        logger = StepLogger()
        logger.log("fill_email", target="label:Email", status="success")
        entries = logger.entries()
        assert len(entries) == 1
        assert entries[0].action == "fill_email"
        assert entries[0].target == "label:Email"
        assert entries[0].status == "success"

    def test_no_value_field_in_entry(self) -> None:
        """StepEntry model must never have a 'value' field."""
        from cv_sender.form_debug import StepEntry

        entry = StepEntry(action="fill_email", target="input[name=email]", status="success")
        data = entry.model_dump()
        assert "value" not in data

    def test_to_dicts_no_value_key(self) -> None:
        logger = StepLogger()
        logger.log("fill_phone", target="placeholder:Phone")
        for d in logger.to_dicts():
            assert "value" not in d

    def test_sensitive_field_names_listed(self) -> None:
        """Spot-check that key sensitive field names are in the constant."""
        assert "email" in _SENSITIVE_FIELDS
        assert "phone" in _SENSITIVE_FIELDS
        assert "first_name" in _SENSITIVE_FIELDS

    def test_multiple_steps_recorded_in_order(self) -> None:
        logger = StepLogger()
        for action in ("open_url", "click_apply_button", "fill_email", "fill_phone"):
            logger.log(action)
        actions = [e.action for e in logger.entries()]
        assert actions == ["open_url", "click_apply_button", "fill_email", "fill_phone"]

    def test_to_dicts_has_timestamp(self) -> None:
        logger = StepLogger()
        logger.log("open_url")
        d = logger.to_dicts()[0]
        assert "timestamp" in d

    def test_empty_logger(self) -> None:
        logger = StepLogger()
        assert logger.entries() == []
        assert logger.to_dicts() == []


# ---------------------------------------------------------------------------
# Debug run ID generation
# ---------------------------------------------------------------------------


class TestDebugRunId:
    def test_run_id_is_uuid(self) -> None:
        record = FormFillDebugRecord()
        # Should not raise; UUID4 is a valid UUID string
        parsed = uuid.UUID(record.run_id)
        assert parsed.version == 4

    def test_each_record_has_unique_run_id(self) -> None:
        ids = {FormFillDebugRecord().run_id for _ in range(10)}
        assert len(ids) == 10


# ---------------------------------------------------------------------------
# Debug paths
# ---------------------------------------------------------------------------


class TestDebugPaths:
    def test_save_creates_run_directory(self, tmp_path: Path) -> None:
        import cv_sender.form_debug as _fd

        _fd._DEBUG_BASE = tmp_path / "debug"
        record = FormFillDebugRecord(offer_id="abc", source="test", filler_name="GenericFiller")
        logger = StepLogger()
        logger.log("open_url")
        saved = save_debug_run(record, logger, None, None)
        run_dir = tmp_path / "debug" / record.run_id
        assert run_dir.is_dir()

    def test_save_writes_metadata(self, tmp_path: Path) -> None:
        import cv_sender.form_debug as _fd

        _fd._DEBUG_BASE = tmp_path / "debug"
        record = FormFillDebugRecord(offer_id="abc", source="rocketjobs.pl")
        saved = save_debug_run(record, StepLogger(), None, None)
        meta = tmp_path / "debug" / record.run_id / "metadata.json"
        assert meta.exists()
        data = json.loads(meta.read_text())
        assert data["offer_id"] == "abc"
        assert data["source"] == "rocketjobs.pl"

    def test_save_step_log_file(self, tmp_path: Path) -> None:
        import cv_sender.form_debug as _fd

        _fd._DEBUG_BASE = tmp_path / "debug"
        record = FormFillDebugRecord()
        logger = StepLogger()
        logger.log("open_url", status="success")
        saved = save_debug_run(record, logger, None, None)
        assert saved.step_log_path
        assert Path(saved.step_log_path).exists()

    def test_save_screenshot_bytes(self, tmp_path: Path) -> None:
        import cv_sender.form_debug as _fd

        _fd._DEBUG_BASE = tmp_path / "debug"
        record = FormFillDebugRecord()
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        saved = save_debug_run(record, StepLogger(), None, fake_png)
        assert saved.screenshot_path
        assert Path(saved.screenshot_path).name == "screenshot.png"
        assert Path(saved.screenshot_path).read_bytes() == fake_png

    def test_save_form_snapshot_file(self, tmp_path: Path) -> None:
        import cv_sender.form_debug as _fd

        _fd._DEBUG_BASE = tmp_path / "debug"
        record = FormFillDebugRecord()
        snapshot = [{"tag": "input", "type": "email", "name": "email"}]
        saved = save_debug_run(record, StepLogger(), snapshot, None)
        assert saved.form_snapshot_path
        data = json.loads(Path(saved.form_snapshot_path).read_text())
        assert data[0]["name"] == "email"

    def test_no_screenshot_when_bytes_none(self, tmp_path: Path) -> None:
        import cv_sender.form_debug as _fd

        _fd._DEBUG_BASE = tmp_path / "debug"
        record = FormFillDebugRecord()
        saved = save_debug_run(record, StepLogger(), None, None)
        assert saved.screenshot_path == ""


# ---------------------------------------------------------------------------
# Metadata serialization
# ---------------------------------------------------------------------------


class TestMetadataSerialization:
    def test_round_trip(self, tmp_path: Path) -> None:
        import cv_sender.form_debug as _fd

        _fd._DEBUG_BASE = tmp_path / "debug"
        record = FormFillDebugRecord(
            offer_id="xyz",
            source="justjoin.it",
            filler_name="JustJoinFiller",
            status="partial",
            fields_filled=["email", "phone"],
            fields_missing=["first_name"],
            warnings=["Login may be required."],
            error="",
        )
        save_debug_run(record, StepLogger(), None, None)
        loaded = load_debug_run(record.run_id)
        assert loaded is not None
        assert loaded.offer_id == "xyz"
        assert loaded.fields_filled == ["email", "phone"]
        assert loaded.fields_missing == ["first_name"]
        assert loaded.warnings == ["Login may be required."]

    def test_load_returns_none_for_missing(self) -> None:
        result = load_debug_run("nonexistent-run-id")
        assert result is None

    def test_load_debug_runs_sorted_newest_first(self, tmp_path: Path) -> None:
        import cv_sender.form_debug as _fd

        _fd._DEBUG_BASE = tmp_path / "debug"
        for i in range(3):
            r = FormFillDebugRecord(
                started_at=datetime(2026, 1, i + 1, tzinfo=UTC),
            )
            save_debug_run(r, StepLogger(), None, None)

        runs = load_debug_runs(limit=10)
        timestamps = [r.started_at.day for r in runs]
        assert timestamps == sorted(timestamps, reverse=True)


# ---------------------------------------------------------------------------
# Form snapshot sanitizer — no input values
# ---------------------------------------------------------------------------


class TestFormSnapshot:
    def _make_page(self, elements: list[dict]) -> MagicMock:
        """Build a mock Playwright page with fake form elements."""
        page = MagicMock()

        mock_els = []
        for el_data in elements:
            el = MagicMock()
            el.is_visible.return_value = True
            el.evaluate.return_value = el_data.get("tag", "input")
            el.get_attribute.side_effect = lambda attr, d=el_data: d.get(attr, None)
            mock_els.append(el)

        page.query_selector_all.return_value = mock_els
        page.query_selector.return_value = None
        return page

    def test_snapshot_returns_list(self) -> None:
        page = self._make_page([
            {"tag": "input", "type": "email", "name": "email"},
        ])
        result = snapshot_form(page)
        assert isinstance(result, list)

    def test_snapshot_no_value_key(self) -> None:
        page = self._make_page([
            {"tag": "input", "type": "text", "name": "first_name"},
            {"tag": "input", "type": "email", "name": "email"},
        ])
        for entry in snapshot_form(page):
            assert "value" not in entry

    def test_snapshot_empty_page(self) -> None:
        page = MagicMock()
        page.query_selector_all.return_value = []
        assert snapshot_form(page) == []

    def test_snapshot_captures_name_and_type(self) -> None:
        page = self._make_page([
            {"tag": "input", "type": "tel", "name": "phone"},
        ])
        result = snapshot_form(page)
        assert len(result) == 1
        assert result[0]["name"] == "phone"
        assert result[0]["type"] == "tel"

    def test_snapshot_skips_invisible(self) -> None:
        page = MagicMock()
        el = MagicMock()
        el.is_visible.return_value = False
        page.query_selector_all.return_value = [el]
        page.query_selector.return_value = None
        result = snapshot_form(page)
        assert result == []

    def test_snapshot_graceful_on_exception(self) -> None:
        page = MagicMock()
        page.query_selector_all.side_effect = Exception("Playwright error")
        result = snapshot_form(page)
        assert result == []


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


class TestDetectionHelpers:
    def _page_with_url(self, url: str) -> MagicMock:
        page = MagicMock()
        page.url = url
        page.title.return_value = "Some job page"
        page.locator.return_value.count.return_value = 0
        return page

    def test_detect_login_wall_by_url(self) -> None:
        page = self._page_with_url("https://example.com/login?next=/apply")
        assert detect_login_wall(page) is True

    def test_detect_login_wall_by_signin_url(self) -> None:
        page = self._page_with_url("https://example.com/signin")
        assert detect_login_wall(page) is True

    def test_detect_login_wall_false_for_clean_url(self) -> None:
        page = self._page_with_url("https://rocketjobs.pl/job/123")
        assert detect_login_wall(page) is False

    def test_detect_captcha_by_selector(self) -> None:
        page = MagicMock()
        page.url = "https://example.com/apply"
        recaptcha = MagicMock()
        recaptcha.count.return_value = 1
        recaptcha.first.is_visible.return_value = True

        def locator_side_effect(sel: str):
            if "recaptcha" in sel or "sitekey" in sel:
                return recaptcha
            m = MagicMock()
            m.count.return_value = 0
            return m

        page.locator.side_effect = locator_side_effect
        assert detect_captcha(page) is True

    def test_detect_captcha_false_for_clean_page(self) -> None:
        page = MagicMock()
        page.url = "https://rocketjobs.pl/job/123"
        page.locator.return_value.count.return_value = 0
        assert detect_captcha(page) is False

    def test_detect_blocked_page_by_title(self) -> None:
        page = MagicMock()
        page.url = "https://example.com/apply"
        page.title.return_value = "Access Denied"
        assert detect_blocked_page(page) is True

    def test_detect_blocked_page_false_for_normal(self) -> None:
        page = MagicMock()
        page.url = "https://rocketjobs.pl/job/123"
        page.title.return_value = "Backend Developer @ ACME"
        assert detect_blocked_page(page) is False


# ---------------------------------------------------------------------------
# FillResult debug fields
# ---------------------------------------------------------------------------


class TestFillResultDebugFields:
    def test_debug_fields_default_empty(self) -> None:
        r = FillResult(status=FillStatus.FAILED, offer_id="x", url="https://x.com")
        assert r.debug_run_id == ""
        assert r.screenshot_path == ""
        assert r.form_snapshot_path == ""
        assert r.step_log_path == ""

    def test_debug_fields_can_be_set(self) -> None:
        r = FillResult(
            status=FillStatus.PARTIAL,
            offer_id="x",
            url="https://x.com",
            debug_run_id="abc-123",
            screenshot_path="data/debug/abc/screenshot.png",
            form_snapshot_path="data/debug/abc/form_snapshot.json",
            step_log_path="data/debug/abc/step_log.json",
        )
        assert r.debug_run_id == "abc-123"
        assert "screenshot.png" in r.screenshot_path


# ---------------------------------------------------------------------------
# FormFillingConfig extended fields
# ---------------------------------------------------------------------------


class TestFormFillingConfig:
    def test_default_values(self) -> None:
        cfg = FormFillingConfig()
        assert cfg.screenshot_on_failure is True
        assert cfg.save_form_snapshot is True
        assert cfg.save_step_log is True
        assert cfg.debug is False

    def test_override_values(self) -> None:
        cfg = FormFillingConfig(debug=True, screenshot_on_failure=False, save_step_log=False)
        assert cfg.debug is True
        assert cfg.screenshot_on_failure is False
        assert cfg.save_step_log is False


# ---------------------------------------------------------------------------
# Step log persistence (round-trip)
# ---------------------------------------------------------------------------


class TestStepLogPersistence:
    def test_round_trip(self, tmp_path: Path) -> None:
        import cv_sender.form_debug as _fd

        _fd._DEBUG_BASE = tmp_path / "debug"
        record = FormFillDebugRecord()
        logger = StepLogger()
        logger.log("open_url", status="success")
        logger.log("fill_email", target="label:Email", status="success")
        logger.log("upload_cv", status="failed")
        save_debug_run(record, logger, None, None)

        entries = load_step_log(record.run_id)
        assert len(entries) == 3
        assert entries[0]["action"] == "open_url"
        assert entries[1]["action"] == "fill_email"
        assert entries[2]["action"] == "upload_cv"
        for e in entries:
            assert "value" not in e

    def test_returns_empty_for_missing_run(self) -> None:
        result = load_step_log("nonexistent-run-id")
        assert result == []


# ---------------------------------------------------------------------------
# Retry service — GenericFiller selected when force_generic=True
# ---------------------------------------------------------------------------


class TestRetryService:
    @pytest.fixture(autouse=True)
    def _setup_offer(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import cv_sender.storage as _storage

        monkeypatch.setattr(_storage, "_DEFAULT_OFFERS", tmp_path / "offers.json")
        monkeypatch.setattr(_storage, "_DEFAULT_APPLICATIONS", tmp_path / "applications.json")
        from cv_sender.storage import add_offer

        self.offer = Offer(url="https://rocketjobs.pl/job/1", title="Dev", company="ACME")
        add_offer(self.offer)

    def test_retry_generic_uses_generic_filler(self) -> None:
        from cv_sender.portals.generic import GenericFiller
        from cv_sender.services import fill_application_form_retry

        ok_result = FillResult(
            status=FillStatus.PARTIAL,
            offer_id=self.offer.id,
            url=self.offer.url,
            fields_filled=["email"],
        )
        with patch.object(GenericFiller, "fill", return_value=ok_result) as mock_fill:
            result = fill_application_form_retry(self.offer.id, force_generic=True)

        mock_fill.assert_called_once()
        assert result.status == FillStatus.PARTIAL

    def test_retry_same_uses_specific_filler(self) -> None:
        from cv_sender.form_filler import fill_application_with_result
        from cv_sender.services import fill_application_form_retry

        ok_result = FillResult(
            status=FillStatus.FILLED,
            offer_id=self.offer.id,
            url=self.offer.url,
            fields_filled=["email", "phone"],
        )
        with patch("cv_sender.form_filler.fill_application_with_result", return_value=ok_result):
            result = fill_application_form_retry(self.offer.id, force_generic=False)

        assert result.status == FillStatus.FILLED

    def test_retry_not_found_returns_failed(self) -> None:
        from cv_sender.services import fill_application_form_retry

        result = fill_application_form_retry("nonexistent-offer-id", force_generic=True)
        assert result.status == FillStatus.FAILED
        assert "not found" in (result.error or "")

    def test_retry_records_application_event(self) -> None:
        from cv_sender.portals.generic import GenericFiller
        from cv_sender.services import fill_application_form_retry
        from cv_sender.storage import load_applications

        ok_result = FillResult(
            status=FillStatus.PARTIAL,
            offer_id=self.offer.id,
            url=self.offer.url,
        )
        with patch.object(GenericFiller, "fill", return_value=ok_result):
            fill_application_form_retry(self.offer.id, force_generic=True)

        apps = load_applications()
        app = next((a for a in apps if a.offer_id == self.offer.id), None)
        assert app is not None
        events = [e.event for e in app.events]
        assert "form_filled_retry" in events


# ---------------------------------------------------------------------------
# Service debug helpers
# ---------------------------------------------------------------------------


class TestServiceDebugHelpers:
    @pytest.fixture(autouse=True)
    def _patch_storage(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import cv_sender.storage as _storage

        monkeypatch.setattr(_storage, "_DEFAULT_OFFERS", tmp_path / "offers.json")
        monkeypatch.setattr(_storage, "_DEFAULT_APPLICATIONS", tmp_path / "applications.json")

    def test_get_debug_runs_empty(self) -> None:
        from cv_sender.services import get_debug_runs

        runs = get_debug_runs()
        assert isinstance(runs, list)

    def test_get_debug_run_returns_none_for_missing(self) -> None:
        from cv_sender.services import get_debug_run

        result = get_debug_run("nonexistent-run-id")
        assert result is None

    def test_get_debug_step_log_returns_list(self) -> None:
        from cv_sender.services import get_debug_step_log

        result = get_debug_step_log("nonexistent-run-id")
        assert result == []

    def test_get_debug_form_snapshot_returns_list(self) -> None:
        from cv_sender.services import get_debug_form_snapshot

        result = get_debug_form_snapshot("nonexistent-run-id")
        assert result == []

    def test_get_debug_runs_with_data(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import cv_sender.form_debug as _fd

        monkeypatch.setattr(_fd, "_DEBUG_BASE", tmp_path / "debug")

        record = FormFillDebugRecord(offer_id="test-offer", source="rocketjobs.pl")
        save_debug_run(record, StepLogger(), None, None)

        from cv_sender.services import get_debug_runs

        runs = get_debug_runs()
        assert any(r.offer_id == "test-offer" for r in runs)
