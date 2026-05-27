"""Tests for interview scheduling: models, storage, service, and calendar payload."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cv_sender.config import CalendarConfig
from cv_sender.models import (
    Application,
    ApplicationStatus,
    Interview,
    InterviewStatus,
    InterviewType,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_storage_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect storage to temp files so tests don't touch real data."""
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_OFFERS", tmp_path / "offers.json")
    monkeypatch.setattr(_storage, "_DEFAULT_APPLICATIONS", tmp_path / "applications.json")
    monkeypatch.setattr(_storage, "_DEFAULT_EMAIL_MATCHES", tmp_path / "email_matches.json")
    monkeypatch.setattr(_storage, "_DEFAULT_INTERVIEWS", tmp_path / "interviews.json")


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return a default Settings for all tests."""
    from cv_sender.config import Settings

    default = Settings()
    monkeypatch.setattr("cv_sender.services.load_settings", lambda: default)
    monkeypatch.setattr("cv_sender.interviews.get_application_by_id", _fake_get_app, raising=False)
    monkeypatch.setattr("cv_sender.interviews.update_application", lambda app, **_: None, raising=False)


def _make_app(**kwargs) -> Application:
    defaults = dict(offer_id="offer-1", title="Frontend Dev", company="ACME")
    defaults.update(kwargs)
    return Application(**defaults)


def _fake_get_app(app_id: str, **_):
    return _make_app(id=app_id, offer_id="offer-1", company="TestCo", title="Dev")


def _future() -> datetime:
    return datetime.now(UTC) + timedelta(days=3)


def _past() -> datetime:
    return datetime.now(UTC) - timedelta(days=3)


# ---------------------------------------------------------------------------
# Model / data tests
# ---------------------------------------------------------------------------


def test_interview_defaults() -> None:
    iv = Interview(application_id="app-1", interview_at=_future())
    assert iv.status == InterviewStatus.SCHEDULED
    assert iv.interview_type == InterviewType.UNKNOWN
    assert iv.source == "manual"
    assert iv.duration_minutes == 60
    assert iv.id  # uuid generated


def test_interview_type_enum_values() -> None:
    assert InterviewType.PHONE == "phone"
    assert InterviewType.VIDEO == "video"
    assert InterviewType.ONSITE == "onsite"
    assert InterviewType.TECHNICAL == "technical"
    assert InterviewType.HR == "hr"
    assert InterviewType.UNKNOWN == "unknown"


def test_interview_status_enum_values() -> None:
    assert InterviewStatus.SCHEDULED == "scheduled"
    assert InterviewStatus.COMPLETED == "completed"
    assert InterviewStatus.CANCELLED == "cancelled"
    assert InterviewStatus.RESCHEDULED == "rescheduled"


# ---------------------------------------------------------------------------
# Storage tests
# ---------------------------------------------------------------------------


def test_storage_add_and_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_INTERVIEWS", tmp_path / "interviews.json")

    from cv_sender.storage import add_interview, load_interviews

    iv = Interview(application_id="app-1", interview_at=_future())
    add_interview(iv)

    loaded = load_interviews()
    assert len(loaded) == 1
    assert loaded[0].id == iv.id


def test_storage_get_by_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_INTERVIEWS", tmp_path / "interviews.json")

    from cv_sender.storage import add_interview, get_interview_by_id

    iv = Interview(application_id="app-1", interview_at=_future())
    add_interview(iv)

    found = get_interview_by_id(iv.id)
    assert found is not None
    assert found.id == iv.id


def test_storage_get_by_id_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_INTERVIEWS", tmp_path / "interviews.json")

    from cv_sender.storage import get_interview_by_id

    assert get_interview_by_id("nonexistent") is None


def test_storage_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_INTERVIEWS", tmp_path / "interviews.json")

    from cv_sender.storage import add_interview, get_interview_by_id, update_interview

    iv = Interview(application_id="app-1", interview_at=_future())
    add_interview(iv)

    updated = iv.model_copy(update={"status": InterviewStatus.COMPLETED})
    update_interview(updated)

    loaded = get_interview_by_id(iv.id)
    assert loaded.status == InterviewStatus.COMPLETED


# ---------------------------------------------------------------------------
# Service function tests
# ---------------------------------------------------------------------------


def test_create_interview_updates_application_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_INTERVIEWS", tmp_path / "interviews.json")

    updated_apps: list[Application] = []

    def mock_update_app(app, **_):
        updated_apps.append(app)

    monkeypatch.setattr("cv_sender.interviews.update_application", mock_update_app)

    from cv_sender.interviews import create_interview

    ok, msg, iv = create_interview(
        "app-1",
        {"interview_at": _future(), "source": "manual"},
    )

    assert ok
    assert iv is not None
    assert updated_apps
    assert updated_apps[-1].status == ApplicationStatus.INTERVIEW
    assert updated_apps[-1].interview_at == iv.interview_at
    assert updated_apps[-1].interview_id == iv.id


def test_create_interview_appends_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_INTERVIEWS", tmp_path / "interviews.json")

    captured: list[Application] = []
    monkeypatch.setattr("cv_sender.interviews.update_application", lambda app, **_: captured.append(app))

    from cv_sender.interviews import create_interview

    create_interview("app-1", {"interview_at": _future()})

    assert captured
    event_names = [e.event for e in captured[-1].events]
    assert "interview_scheduled" in event_names


def test_create_interview_bad_data_returns_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_INTERVIEWS", tmp_path / "interviews.json")

    from cv_sender.interviews import create_interview

    ok, msg, iv = create_interview("app-1", {"interview_at": "not-a-datetime"})
    assert not ok
    assert iv is None


def test_create_interview_missing_app_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cv_sender.interviews.get_application_by_id", lambda _id: None)

    from cv_sender.interviews import create_interview

    ok, msg, iv = create_interview("no-such-app", {"interview_at": _future()})
    assert not ok
    assert iv is None


def test_list_upcoming_interviews(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_INTERVIEWS", tmp_path / "interviews.json")

    from cv_sender.storage import add_interview
    from cv_sender.interviews import list_upcoming_interviews

    future_iv = Interview(application_id="app-1", interview_at=_future())
    past_iv = Interview(application_id="app-1", interview_at=_past())
    add_interview(future_iv)
    add_interview(past_iv)

    upcoming = list_upcoming_interviews()
    ids = [i.id for i in upcoming]
    assert future_iv.id in ids
    assert past_iv.id not in ids


def test_list_past_interviews(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_INTERVIEWS", tmp_path / "interviews.json")

    from cv_sender.storage import add_interview
    from cv_sender.interviews import list_past_interviews

    future_iv = Interview(application_id="app-1", interview_at=_future())
    past_iv = Interview(application_id="app-1", interview_at=_past())
    add_interview(future_iv)
    add_interview(past_iv)

    past = list_past_interviews()
    ids = [i.id for i in past]
    assert past_iv.id in ids
    assert future_iv.id not in ids


def test_mark_interview_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_INTERVIEWS", tmp_path / "interviews.json")

    from cv_sender.storage import add_interview, get_interview_by_id
    from cv_sender.interviews import mark_interview_completed

    iv = Interview(application_id="app-1", interview_at=_future())
    add_interview(iv)

    ok, msg = mark_interview_completed(iv.id)
    assert ok
    reloaded = get_interview_by_id(iv.id)
    assert reloaded.status == InterviewStatus.COMPLETED


def test_cancel_interview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_INTERVIEWS", tmp_path / "interviews.json")

    from cv_sender.storage import add_interview, get_interview_by_id
    from cv_sender.interviews import cancel_interview

    iv = Interview(application_id="app-1", interview_at=_future())
    add_interview(iv)

    ok, msg = cancel_interview(iv.id)
    assert ok
    reloaded = get_interview_by_id(iv.id)
    assert reloaded.status == InterviewStatus.CANCELLED


def test_reschedule_updates_interview_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_INTERVIEWS", tmp_path / "interviews.json")
    monkeypatch.setattr("cv_sender.interviews.update_application", lambda app, **_: None)

    from cv_sender.storage import add_interview, get_interview_by_id
    from cv_sender.interviews import reschedule_interview

    iv = Interview(application_id="app-1", interview_at=_future())
    add_interview(iv)

    new_dt = datetime.now(UTC) + timedelta(days=7)
    ok, msg = reschedule_interview(iv.id, new_dt)
    assert ok
    reloaded = get_interview_by_id(iv.id)
    assert reloaded.interview_at == new_dt
    assert reloaded.status == InterviewStatus.RESCHEDULED


def test_reschedule_missing_interview() -> None:
    from cv_sender.interviews import reschedule_interview

    ok, msg = reschedule_interview("nonexistent", datetime.now(UTC) + timedelta(days=1))
    assert not ok


def test_get_interview_for_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_INTERVIEWS", tmp_path / "interviews.json")

    from cv_sender.storage import add_interview
    from cv_sender.interviews import get_interview_for_application

    iv = Interview(application_id="app-42", interview_at=_future())
    add_interview(iv)

    found = get_interview_for_application("app-42")
    assert found is not None
    assert found.id == iv.id

    assert get_interview_for_application("unknown") is None


def test_schedule_from_gmail_match_missing_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cv_sender.interviews.get_email_match_by_id", lambda _: None, raising=False)

    # Patch the import inside schedule_interview_from_email_match
    with patch("cv_sender.storage.get_email_match_by_id", return_value=None):
        from cv_sender.interviews import schedule_interview_from_email_match

        ok, msg, iv = schedule_interview_from_email_match("no-match", {"interview_at": _future()})
        assert not ok
        assert iv is None


# ---------------------------------------------------------------------------
# Calendar event payload tests (pure, no I/O)
# ---------------------------------------------------------------------------


def test_calendar_event_payload_generation() -> None:
    from cv_sender.calendar_integration import build_calendar_event_body

    cfg = CalendarConfig(
        enabled=True,
        timezone="Europe/Warsaw",
        reminder_minutes_before=[1440, 60],
        add_reminders=True,
    )
    iv = Interview(
        application_id="app-1",
        interview_at=datetime(2025, 6, 15, 10, 0, tzinfo=UTC),
        duration_minutes=60,
        company="ACME",
        title="Senior Dev",
        notes="Bring portfolio",
        meeting_url="https://meet.example.com/xyz",
    )

    body = build_calendar_event_body(iv, cfg)

    assert body["summary"] == "Interview: ACME — Senior Dev"
    assert "ACME" in body["description"]
    assert "Senior Dev" in body["description"]
    assert body["start"]["timeZone"] == "Europe/Warsaw"
    assert body["end"]["timeZone"] == "Europe/Warsaw"
    assert body["location"] == "https://meet.example.com/xyz"
    assert body["reminders"]["useDefault"] is False
    overrides = body["reminders"]["overrides"]
    assert any(r["minutes"] == 1440 for r in overrides)
    assert any(r["minutes"] == 60 for r in overrides)


def test_calendar_event_payload_no_reminders() -> None:
    from cv_sender.calendar_integration import build_calendar_event_body

    cfg = CalendarConfig(add_reminders=False, reminder_minutes_before=[])
    iv = Interview(
        application_id="app-1",
        interview_at=datetime(2025, 6, 15, 10, 0, tzinfo=UTC),
        company="X",
        title="Y",
    )
    body = build_calendar_event_body(iv, cfg)
    assert body["reminders"]["useDefault"] is True


def test_calendar_event_payload_location_fallback() -> None:
    """meeting_url takes priority; falls back to location."""
    from cv_sender.calendar_integration import build_calendar_event_body

    cfg = CalendarConfig()
    iv_url = Interview(
        application_id="app-1",
        interview_at=_future(),
        company="A",
        title="B",
        meeting_url="https://zoom.us/j/1234",
        location="Warsaw Office",
    )
    body = build_calendar_event_body(iv_url, cfg)
    assert body["location"] == "https://zoom.us/j/1234"

    iv_loc = Interview(
        application_id="app-1",
        interview_at=_future(),
        company="A",
        title="B",
        location="Warsaw Office",
    )
    body_loc = build_calendar_event_body(iv_loc, cfg)
    assert body_loc["location"] == "Warsaw Office"


def test_calendar_disabled_does_not_crash() -> None:
    """CalendarConfig(enabled=False) must not crash imports or function calls."""
    from cv_sender.calendar_integration import is_calendar_configured, is_calendar_authenticated

    cfg = CalendarConfig(enabled=False)
    assert not is_calendar_configured(cfg)
    # is_calendar_authenticated checks only the token file existence
    assert not is_calendar_authenticated(cfg)


# ---------------------------------------------------------------------------
# No-auto-calendar-event safety invariant
# ---------------------------------------------------------------------------


def test_no_calendar_event_without_explicit_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """create_interview must NOT call the calendar API when create_calendar_event=False."""
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_INTERVIEWS", tmp_path / "interviews.json")
    monkeypatch.setattr("cv_sender.interviews.update_application", lambda app, **_: None)

    mock_create = MagicMock()
    monkeypatch.setattr(
        "cv_sender.interviews.create_calendar_event_for_interview",
        mock_create,
        raising=False,
    )

    from cv_sender.interviews import create_interview

    ok, _, iv = create_interview(
        "app-1",
        {"interview_at": _future()},
        create_calendar_event=False,
    )
    assert ok
    mock_create.assert_not_called()


# ---------------------------------------------------------------------------
# Backward compat: Application without interview_id
# ---------------------------------------------------------------------------


def test_backward_compatibility_application_without_interview_id() -> None:
    """Application objects that don't have interview_id should still load fine."""
    raw = {
        "id": "app-old",
        "offer_id": "offer-old",
        "title": "Old Role",
        "company": "Legacy Corp",
    }
    app = Application.model_validate(raw)
    assert app.interview_id == ""
    assert app.calendar_event_id == ""


def test_calendar_config_defaults() -> None:
    cfg = CalendarConfig()
    assert cfg.enabled is False
    assert cfg.create_calendar_events is False
    assert cfg.add_reminders is True
    assert cfg.reminder_minutes_before == [1440, 60]
    assert cfg.default_interview_duration_minutes == 60
    assert cfg.calendar_id == "primary"
