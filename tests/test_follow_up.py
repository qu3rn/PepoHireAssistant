"""Tests for follow-up tracking: date calculation, service functions, events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from cv_sender.config import FollowUpConfig
from cv_sender.follow_up import (
    calculate_follow_up_due,
    generate_follow_up_message,
    is_follow_up_due,
    is_stale,
)
from cv_sender.models import Application, ApplicationEvent, ApplicationStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_storage_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect storage to temp files so tests don't touch real data."""
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_OFFERS", tmp_path / "offers.json")
    monkeypatch.setattr(_storage, "_DEFAULT_APPLICATIONS", tmp_path / "applications.json")


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return a default Settings for all tests."""
    from cv_sender.config import Settings

    default = Settings()
    monkeypatch.setattr("cv_sender.services.load_settings", lambda: default)
    monkeypatch.setattr("cv_sender.services.load_profile", lambda: __import__("cv_sender.config", fromlist=["Profile"]).Profile())


def _cfg(**kwargs) -> FollowUpConfig:
    defaults = dict(
        enabled=True,
        default_follow_up_after_days=5,
        mark_no_response_after_days=14,
        show_due_within_days=3,
        allow_weekend_due_dates=False,
    )
    defaults.update(kwargs)
    return FollowUpConfig(**defaults)


def _make_app(**kwargs) -> Application:
    defaults = dict(offer_id="offer-1", title="Frontend Dev", company="ACME")
    defaults.update(kwargs)
    return Application(**defaults)


def _monday() -> datetime:
    """Return a Monday at noon UTC."""
    dt = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)  # Monday 2026-05-25
    assert dt.weekday() == 0
    return dt


# ---------------------------------------------------------------------------
# calculate_follow_up_due
# ---------------------------------------------------------------------------


class TestCalculateFollowUpDue:
    def test_adds_correct_days(self):
        sent = _monday()
        cfg = _cfg(default_follow_up_after_days=5, allow_weekend_due_dates=True)
        due = calculate_follow_up_due(sent, cfg)
        # 5 days after Monday = Saturday
        assert (due - sent).days == 5

    def test_skips_weekend_by_default(self):
        # Monday + 5 days = Saturday → should roll to Monday
        sent = _monday()
        cfg = _cfg(default_follow_up_after_days=5, allow_weekend_due_dates=False)
        due = calculate_follow_up_due(sent, cfg)
        # Saturday + 2 = Monday
        assert due.weekday() == 0  # Monday
        assert (due - sent).days == 7

    def test_no_skip_when_weekday(self):
        # Monday + 4 days = Friday → no adjustment needed
        sent = _monday()
        cfg = _cfg(default_follow_up_after_days=4, allow_weekend_due_dates=False)
        due = calculate_follow_up_due(sent, cfg)
        assert due.weekday() == 4  # Friday
        assert (due - sent).days == 4

    def test_allow_weekend_due_dates_returns_saturday(self):
        sent = _monday()
        cfg = _cfg(default_follow_up_after_days=5, allow_weekend_due_dates=True)
        due = calculate_follow_up_due(sent, cfg)
        assert due.weekday() == 5  # Saturday

    def test_returns_utc(self):
        sent = _monday()
        due = calculate_follow_up_due(sent, _cfg())
        assert due.tzinfo is not None

    def test_sunday_rolls_to_monday(self):
        # Monday + 6 = Sunday → rolls to Monday (+7)
        sent = _monday()
        cfg = _cfg(default_follow_up_after_days=6, allow_weekend_due_dates=False)
        due = calculate_follow_up_due(sent, cfg)
        assert due.weekday() == 0


# ---------------------------------------------------------------------------
# is_follow_up_due
# ---------------------------------------------------------------------------


class TestIsFollowUpDue:
    def test_true_when_past_due(self):
        app = _make_app(follow_up_due_at=datetime(2026, 1, 1, tzinfo=UTC))
        assert is_follow_up_due(app, now=datetime(2026, 6, 1, tzinfo=UTC))

    def test_false_when_not_yet_due(self):
        app = _make_app(follow_up_due_at=datetime(2030, 1, 1, tzinfo=UTC))
        assert not is_follow_up_due(app, now=datetime(2026, 6, 1, tzinfo=UTC))

    def test_false_when_no_due_date(self):
        app = _make_app()
        assert not is_follow_up_due(app)

    def test_false_when_snoozed(self):
        now = datetime(2026, 6, 1, tzinfo=UTC)
        app = _make_app(
            follow_up_due_at=datetime(2026, 5, 1, tzinfo=UTC),
            reminder_snoozed_until=datetime(2026, 6, 5, tzinfo=UTC),
        )
        assert not is_follow_up_due(app, now=now)

    def test_true_when_snooze_expired(self):
        now = datetime(2026, 6, 10, tzinfo=UTC)
        app = _make_app(
            follow_up_due_at=datetime(2026, 5, 1, tzinfo=UTC),
            reminder_snoozed_until=datetime(2026, 6, 5, tzinfo=UTC),
        )
        assert is_follow_up_due(app, now=now)


# ---------------------------------------------------------------------------
# is_stale
# ---------------------------------------------------------------------------


class TestIsStale:
    def test_true_after_threshold(self):
        sent = datetime(2026, 5, 1, tzinfo=UTC)
        app = _make_app(status=ApplicationStatus.SENT, sent_at=sent, last_contact_at=sent)
        now = sent + timedelta(days=15)
        assert is_stale(app, _cfg(mark_no_response_after_days=14), now=now)

    def test_false_before_threshold(self):
        sent = datetime(2026, 5, 1, tzinfo=UTC)
        app = _make_app(status=ApplicationStatus.SENT, sent_at=sent, last_contact_at=sent)
        now = sent + timedelta(days=10)
        assert not is_stale(app, _cfg(mark_no_response_after_days=14), now=now)

    def test_false_for_non_sent_status(self):
        app = _make_app(status=ApplicationStatus.REJECTED, last_contact_at=datetime(2026, 1, 1, tzinfo=UTC))
        now = datetime(2026, 6, 1, tzinfo=UTC)
        assert not is_stale(app, _cfg(), now=now)

    def test_false_when_no_contact_date(self):
        app = _make_app(status=ApplicationStatus.SENT)
        assert not is_stale(app, _cfg())


# ---------------------------------------------------------------------------
# mark_application_sent (service)
# ---------------------------------------------------------------------------


class TestMarkApplicationSent:
    def _save_and_get(self, app: Application) -> Application:
        from cv_sender.storage import add_application, get_application_by_id

        add_application(app)
        return get_application_by_id(app.id)

    def test_sets_sent_at(self):
        from cv_sender.services import mark_application_sent

        app = self._save_and_get(_make_app())
        ok, msg = mark_application_sent(app.id)
        assert ok
        from cv_sender.storage import get_application_by_id

        updated = get_application_by_id(app.id)
        assert updated.status == ApplicationStatus.SENT
        assert updated.sent_at is not None

    def test_sets_follow_up_due_at(self):
        from cv_sender.services import mark_application_sent
        from cv_sender.storage import get_application_by_id

        app = self._save_and_get(_make_app())
        ok, _ = mark_application_sent(app.id)
        assert ok
        updated = get_application_by_id(app.id)
        assert updated.follow_up_due_at is not None
        assert updated.next_action_type == "follow_up"

    def test_appends_events(self):
        from cv_sender.services import mark_application_sent
        from cv_sender.storage import get_application_by_id

        app = self._save_and_get(_make_app())
        mark_application_sent(app.id)
        updated = get_application_by_id(app.id)
        event_types = [e.event for e in updated.events]
        assert "status_changed" in event_types
        assert "follow_up_due_created" in event_types

    def test_not_found_returns_error(self):
        from cv_sender.services import mark_application_sent

        ok, msg = mark_application_sent("nonexistent")
        assert not ok


# ---------------------------------------------------------------------------
# mark_follow_up_sent (service)
# ---------------------------------------------------------------------------


class TestMarkFollowUpSent:
    def test_sets_status_and_timestamp(self):
        from cv_sender.services import mark_follow_up_sent
        from cv_sender.storage import add_application, get_application_by_id

        app = _make_app(status=ApplicationStatus.SENT, sent_at=datetime.now(UTC))
        add_application(app)
        ok, _ = mark_follow_up_sent(app.id)
        assert ok
        updated = get_application_by_id(app.id)
        assert updated.status == ApplicationStatus.FOLLOW_UP_SENT
        assert updated.follow_up_sent_at is not None
        event_types = [e.event for e in updated.events]
        assert "follow_up_sent" in event_types


# ---------------------------------------------------------------------------
# snooze_application_reminder (service)
# ---------------------------------------------------------------------------


class TestSnoozeApplicationReminder:
    def test_sets_snoozed_until(self):
        from cv_sender.services import snooze_application_reminder
        from cv_sender.storage import add_application, get_application_by_id

        app = _make_app()
        add_application(app)
        ok, msg = snooze_application_reminder(app.id, 3)
        assert ok
        updated = get_application_by_id(app.id)
        assert updated.reminder_snoozed_until is not None
        expected = datetime.now(UTC) + timedelta(days=3)
        assert abs((updated.reminder_snoozed_until - expected).total_seconds()) < 5
        event_types = [e.event for e in updated.events]
        assert "reminder_snoozed" in event_types


# ---------------------------------------------------------------------------
# schedule_interview (service)
# ---------------------------------------------------------------------------


class TestScheduleInterview:
    def test_sets_interview_date_and_status(self):
        from cv_sender.services import schedule_interview
        from cv_sender.storage import add_application, get_application_by_id

        app = _make_app()
        add_application(app)
        iv_at = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
        ok, msg = schedule_interview(app.id, iv_at, note="Video call")
        assert ok
        updated = get_application_by_id(app.id)
        assert updated.status == ApplicationStatus.INTERVIEW
        assert updated.interview_at == iv_at
        assert updated.next_action_type == "interview"
        event_types = [e.event for e in updated.events]
        assert "interview_scheduled" in event_types


# ---------------------------------------------------------------------------
# get_follow_up_due_applications
# ---------------------------------------------------------------------------


class TestGetFollowUpDueApplications:
    def test_returns_overdue_applications(self):
        from cv_sender.services import get_follow_up_due_applications
        from cv_sender.storage import add_application

        past_due = _make_app(
            follow_up_due_at=datetime(2026, 1, 1, tzinfo=UTC),
            status=ApplicationStatus.SENT,
        )
        not_due = _make_app(
            follow_up_due_at=datetime(2030, 1, 1, tzinfo=UTC),
            status=ApplicationStatus.SENT,
        )
        add_application(past_due)
        add_application(not_due)

        due = get_follow_up_due_applications(now=datetime(2026, 6, 1, tzinfo=UTC))
        due_ids = [a.id for a in due]
        assert past_due.id in due_ids
        assert not_due.id not in due_ids


# ---------------------------------------------------------------------------
# get_stale_applications
# ---------------------------------------------------------------------------


class TestGetStaleApplications:
    def test_returns_stale_applications(self):
        from cv_sender.services import get_stale_applications
        from cv_sender.storage import add_application

        sent_long_ago = datetime(2026, 4, 1, tzinfo=UTC)
        stale = _make_app(status=ApplicationStatus.SENT, sent_at=sent_long_ago, last_contact_at=sent_long_ago)
        fresh = _make_app(status=ApplicationStatus.SENT, sent_at=datetime(2026, 5, 29, tzinfo=UTC))
        add_application(stale)
        add_application(fresh)

        result = get_stale_applications(now=datetime(2026, 6, 1, tzinfo=UTC))
        ids = [a.id for a in result]
        assert stale.id in ids
        assert fresh.id not in ids


# ---------------------------------------------------------------------------
# backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_old_application_without_new_fields_loads(self):
        """Applications without follow-up fields should load without error."""
        import json
        from pathlib import Path

        from cv_sender.storage import load_applications

        # Write minimal old-style application JSON
        raw = [
            {
                "id": "old-app-1",
                "offer_id": "offer-1",
                "source": "generic",
                "url": "https://example.com",
                "company": "Old Corp",
                "title": "Dev",
                "status": "sent",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "events": [],
            }
        ]
        import cv_sender.storage as _storage

        _storage._DEFAULT_APPLICATIONS.parent.mkdir(parents=True, exist_ok=True)
        _storage._DEFAULT_APPLICATIONS.write_text(json.dumps(raw), encoding="utf-8")

        apps = load_applications()
        assert len(apps) == 1
        app = apps[0]
        assert app.sent_at is None
        assert app.follow_up_due_at is None
        assert app.follow_up_sent_at is None
        assert app.status == ApplicationStatus.SENT


# ---------------------------------------------------------------------------
# generate_follow_up_message
# ---------------------------------------------------------------------------


class TestGenerateFollowUpMessage:
    def test_includes_company_and_title(self):
        app = _make_app(
            company="TechCorp",
            title="Senior Dev",
            sent_at=datetime(2026, 5, 1, tzinfo=UTC),
        )
        msg = generate_follow_up_message(app, candidate_name="Jan Kowalski")
        assert "TechCorp" in msg
        assert "Senior Dev" in msg

    def test_no_crash_on_empty_app(self):
        app = _make_app()
        msg = generate_follow_up_message(app)
        assert isinstance(msg, str)
        assert len(msg) > 0
