"""Tests for Rapid Apply session service and apply_queue session helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cv_sender.apply_queue import (
    advance_session,
    get_active_queue_items,
)
from cv_sender.models import (
    Application,
    ApplicationStatus,
    ApplyQueueItem,
    ApplyQueueItemStatus,
    FillResult,
    FillStatus,
)
from cv_sender.rapid_apply_service import (
    SKIP_REASONS,
    QualityStatus,
    SessionStats,
    do_mark_sent,
    do_skip,
    get_session_stats,
    run_quality_check,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(
    item_id: str = "item1",
    offer_id: str = "offer1",
    priority: float = 70.0,
    status: ApplyQueueItemStatus = ApplyQueueItemStatus.QUEUED,
    score: int = 70,
    source: str = "justjoin",
) -> ApplyQueueItem:
    return ApplyQueueItem(
        id=item_id,
        offer_id=offer_id,
        title="React Developer",
        company="ACME",
        source=source,
        url=f"https://example.com/{offer_id}",
        score=score,
        priority_score=priority,
        status=status,
    )


def _make_fill_result(
    status: FillStatus = FillStatus.FILLED,
    fields_filled: list | None = None,
    fields_missing: list | None = None,
    warnings: list | None = None,
) -> FillResult:
    return FillResult(
        status=status,
        fields_filled=fields_filled or ["name", "email"],
        fields_missing=fields_missing or [],
        warnings=warnings or [],
    )


def _save_queue(items: list[ApplyQueueItem], tmp_path: Path) -> Path:
    from cv_sender.storage import save_apply_queue  # noqa: PLC0415

    path = tmp_path / "queue.json"
    save_apply_queue(items, path)
    return path


# ---------------------------------------------------------------------------
# run_quality_check
# ---------------------------------------------------------------------------


class TestRunQualityCheck:
    def test_filled_no_missing_is_ready(self):
        result = _make_fill_result(status=FillStatus.FILLED)
        quality = run_quality_check(result)
        assert quality.badge == "ready"
        assert not quality.warnings

    def test_partial_fill_is_review_needed(self):
        result = _make_fill_result(
            status=FillStatus.PARTIAL,
            fields_missing=["phone"],
        )
        quality = run_quality_check(result)
        assert quality.badge == "review_needed"

    def test_failed_fill_is_not_ready(self):
        result = _make_fill_result(status=FillStatus.FAILED)
        quality = run_quality_check(result)
        assert quality.badge == "not_ready"

    def test_filled_with_missing_fields_is_review_needed(self):
        result = _make_fill_result(
            status=FillStatus.FILLED,
            fields_missing=["cover_letter"],
        )
        quality = run_quality_check(result)
        assert quality.badge == "review_needed"

    def test_warnings_in_result_show_in_quality(self):
        result = _make_fill_result(warnings=["CV upload failed"])
        quality = run_quality_check(result)
        assert any("CV upload failed" in w for w in quality.warnings)
        assert quality.badge == "review_needed"

    def test_checklist_non_empty(self):
        result = _make_fill_result()
        quality = run_quality_check(result)
        assert len(quality.checklist) >= 1

    def test_generated_answers_appear_in_checklist(self):
        result = FillResult(
            status=FillStatus.FILLED,
            generated_answers=[{"question": "Why?", "answer": "Because."}],
        )
        quality = run_quality_check(result)
        assert any("Generated answers" in line for line in quality.checklist)


# ---------------------------------------------------------------------------
# get_active_queue_items (filtering)
# ---------------------------------------------------------------------------


class TestGetActiveQueueItems:
    def test_returns_queued_items(self, tmp_path):
        items = [_make_item("a", status=ApplyQueueItemStatus.QUEUED)]
        path = _save_queue(items, tmp_path)
        active = get_active_queue_items(path)
        assert len(active) == 1

    def test_returns_filled_items(self, tmp_path):
        items = [_make_item("a", status=ApplyQueueItemStatus.FILLED)]
        path = _save_queue(items, tmp_path)
        active = get_active_queue_items(path)
        assert len(active) == 1

    def test_returns_failed_items_by_default(self, tmp_path):
        items = [_make_item("a", status=ApplyQueueItemStatus.FAILED)]
        path = _save_queue(items, tmp_path)
        active = get_active_queue_items(path)
        assert len(active) == 1

    def test_excludes_sent(self, tmp_path):
        items = [_make_item("a", status=ApplyQueueItemStatus.SENT)]
        path = _save_queue(items, tmp_path)
        active = get_active_queue_items(path)
        assert len(active) == 0

    def test_excludes_skipped(self, tmp_path):
        items = [_make_item("a", status=ApplyQueueItemStatus.SKIPPED)]
        path = _save_queue(items, tmp_path)
        active = get_active_queue_items(path)
        assert len(active) == 0

    def test_min_score_filter(self, tmp_path):
        items = [
            _make_item("hi", score=80, priority=80.0),
            _make_item("lo", offer_id="o2", score=30, priority=30.0),
        ]
        path = _save_queue(items, tmp_path)
        active = get_active_queue_items(path, min_score=60)
        assert len(active) == 1
        assert active[0].score == 80

    def test_source_filter(self, tmp_path):
        items = [
            _make_item("a", source="justjoin"),
            _make_item("b", offer_id="o2", source="pracuj"),
        ]
        path = _save_queue(items, tmp_path)
        active = get_active_queue_items(path, source_filter="justjoin")
        assert len(active) == 1
        assert active[0].source == "justjoin"

    def test_exclude_failed_filter(self, tmp_path):
        items = [
            _make_item("a", status=ApplyQueueItemStatus.QUEUED),
            _make_item("b", offer_id="o2", status=ApplyQueueItemStatus.FAILED),
        ]
        path = _save_queue(items, tmp_path)
        active = get_active_queue_items(path, exclude_failed=True)
        assert len(active) == 1
        assert active[0].id == "a"

    def test_sorted_by_priority_descending(self, tmp_path):
        items = [
            _make_item("lo", offer_id="o1", priority=20.0),
            _make_item("hi", offer_id="o2", priority=90.0),
            _make_item("mid", offer_id="o3", priority=55.0),
        ]
        path = _save_queue(items, tmp_path)
        active = get_active_queue_items(path)
        priorities = [i.priority_score for i in active]
        assert priorities == sorted(priorities, reverse=True)


# ---------------------------------------------------------------------------
# advance_session
# ---------------------------------------------------------------------------


class TestAdvanceSession:
    def test_returns_next_item(self, tmp_path):
        items = [
            _make_item("a", offer_id="o1", priority=90.0),
            _make_item("b", offer_id="o2", priority=80.0),
            _make_item("c", offer_id="o3", priority=70.0),
        ]
        path = _save_queue(items, tmp_path)
        nxt = advance_session("a", path)
        assert nxt is not None
        assert nxt.id == "b"

    def test_returns_none_at_end(self, tmp_path):
        items = [_make_item("a", offer_id="o1")]
        path = _save_queue(items, tmp_path)
        nxt = advance_session("a", path)
        assert nxt is None

    def test_returns_first_if_current_not_found(self, tmp_path):
        items = [
            _make_item("a", offer_id="o1", priority=90.0),
            _make_item("b", offer_id="o2", priority=80.0),
        ]
        path = _save_queue(items, tmp_path)
        nxt = advance_session("nonexistent", path)
        assert nxt is not None
        assert nxt.id == "a"

    def test_skips_terminal_items(self, tmp_path):
        items = [
            _make_item("a", offer_id="o1", priority=90.0),
            _make_item("b", offer_id="o2", priority=80.0, status=ApplyQueueItemStatus.SENT),
            _make_item("c", offer_id="o3", priority=70.0),
        ]
        path = _save_queue(items, tmp_path)
        # b is terminal, advance from a should reach c directly
        nxt = advance_session("a", path)
        assert nxt is not None
        assert nxt.id == "c"


# ---------------------------------------------------------------------------
# do_mark_sent (mocked services)
# ---------------------------------------------------------------------------


class TestDoMarkSent:
    def test_marks_queue_item_sent(self, tmp_path):
        item = _make_item("item1", "offer1")

        with (
            patch("cv_sender.rapid_apply_service.get_queue_item_by_id", return_value=item),
            patch("cv_sender.rapid_apply_service.mark_queue_item_status") as mock_mark,
            patch("cv_sender.services._find_application_for_offer", return_value=None),
            patch("cv_sender.rapid_apply_service.advance_session", return_value=None),
        ):
            result = do_mark_sent("item1")

        assert result.success is True
        mock_mark.assert_called_with("item1", ApplyQueueItemStatus.SENT)

    def test_mark_sent_calls_application_service(self, tmp_path):
        item = _make_item("item1", "offer1")
        mock_app = MagicMock()
        mock_app.id = "app1"

        with (
            patch("cv_sender.rapid_apply_service.get_queue_item_by_id", return_value=item),
            patch("cv_sender.rapid_apply_service.mark_queue_item_status"),
            patch("cv_sender.services._find_application_for_offer", return_value=mock_app),
            patch("cv_sender.services.mark_application_sent", return_value=(True, "Marked as sent.")) as mock_sent,
            patch("cv_sender.rapid_apply_service.advance_session", return_value=None),
        ):
            result = do_mark_sent("item1")

        mock_sent.assert_called_once_with("app1")
        assert result.success is True

    def test_mark_sent_returns_next_item(self, tmp_path):
        item = _make_item("item1", "offer1")
        next_item = _make_item("item2", "offer2")

        with (
            patch("cv_sender.rapid_apply_service.get_queue_item_by_id", return_value=item),
            patch("cv_sender.rapid_apply_service.mark_queue_item_status"),
            patch("cv_sender.services._find_application_for_offer", return_value=None),
            patch("cv_sender.rapid_apply_service.advance_session", return_value=next_item),
        ):
            result = do_mark_sent("item1")

        assert result.next_item is not None
        assert result.next_item.id == "item2"

    def test_mark_sent_returns_failure_for_missing_item(self):
        with patch("cv_sender.rapid_apply_service.get_queue_item_by_id", return_value=None):
            result = do_mark_sent("nonexistent")
        assert result.success is False

    def test_no_auto_submit(self, tmp_path):
        """Verifies that mark_sent does not call fill/submit logic."""
        item = _make_item("item1", "offer1")
        with (
            patch("cv_sender.rapid_apply_service.get_queue_item_by_id", return_value=item),
            patch("cv_sender.rapid_apply_service.mark_queue_item_status"),
            patch("cv_sender.services._find_application_for_offer", return_value=None),
            patch("cv_sender.services.fill_application_form") as mock_fill,
            patch("cv_sender.rapid_apply_service.advance_session", return_value=None),
        ):
            do_mark_sent("item1")
            # fill_application_form must NOT be called
            mock_fill.assert_not_called()


# ---------------------------------------------------------------------------
# do_skip
# ---------------------------------------------------------------------------


class TestDoSkip:
    def test_marks_queue_item_skipped(self, tmp_path):
        item = _make_item("item1", "offer1")
        with (
            patch("cv_sender.rapid_apply_service.get_queue_item_by_id", return_value=item),
            patch("cv_sender.storage.update_queue_item") as mock_update,
            patch("cv_sender.services._find_application_for_offer", return_value=None),
            patch("cv_sender.rapid_apply_service.advance_session", return_value=None),
        ):
            result = do_skip("item1", reason="low salary")

        assert result.success is True
        mock_update.assert_called_once()
        updated = mock_update.call_args[0][0]
        assert updated.status == ApplyQueueItemStatus.SKIPPED

    def test_skip_stores_reason_in_warnings(self, tmp_path):
        item = _make_item("item1", "offer1")
        with (
            patch("cv_sender.rapid_apply_service.get_queue_item_by_id", return_value=item),
            patch("cv_sender.storage.update_queue_item") as mock_update,
            patch("cv_sender.services._find_application_for_offer", return_value=None),
            patch("cv_sender.rapid_apply_service.advance_session", return_value=None),
        ):
            do_skip("item1", reason="broken form")

        updated = mock_update.call_args[0][0]
        assert any("broken form" in w for w in updated.warnings)

    def test_skip_no_reason_still_skips(self):
        item = _make_item("item1", "offer1")
        with (
            patch("cv_sender.rapid_apply_service.get_queue_item_by_id", return_value=item),
            patch("cv_sender.rapid_apply_service.mark_queue_item_status") as mock_mark,
            patch("cv_sender.services._find_application_for_offer", return_value=None),
            patch("cv_sender.rapid_apply_service.advance_session", return_value=None),
        ):
            result = do_skip("item1", reason="")

        assert result.success is True
        mock_mark.assert_called_once_with("item1", ApplyQueueItemStatus.SKIPPED)

    def test_skip_appends_event_to_application(self):
        from cv_sender.models import Application  # noqa: PLC0415

        item = _make_item("item1", "offer1")
        real_app = Application(
            id="app1",
            offer_id="offer1",
            company="ACME",
            title="React Dev",
            url="https://example.com/offer1",
            status=ApplicationStatus.NEW,
        )

        with (
            patch("cv_sender.rapid_apply_service.get_queue_item_by_id", return_value=item),
            patch("cv_sender.storage.update_queue_item"),
            patch("cv_sender.services._find_application_for_offer", return_value=real_app),
            patch("cv_sender.rapid_apply_service.update_application") as mock_update_app,
            patch("cv_sender.rapid_apply_service.advance_session", return_value=None),
        ):
            do_skip("item1", reason="not interested")

        mock_update_app.assert_called_once()
        updated_app = mock_update_app.call_args[0][0]
        assert any("skipped" in e.event.lower() for e in updated_app.events)

    def test_skip_returns_none_for_missing_item(self):
        with patch("cv_sender.rapid_apply_service.get_queue_item_by_id", return_value=None):
            result = do_skip("nonexistent")
        assert result.success is False

    def test_skip_advances_to_next(self):
        item = _make_item("item1", "offer1")
        next_item = _make_item("item2", "offer2")
        with (
            patch("cv_sender.rapid_apply_service.get_queue_item_by_id", return_value=item),
            patch("cv_sender.storage.update_queue_item"),
            patch("cv_sender.services._find_application_for_offer", return_value=None),
            patch("cv_sender.rapid_apply_service.advance_session", return_value=next_item),
        ):
            result = do_skip("item1")
        assert result.next_item is not None
        assert result.next_item.id == "item2"


# ---------------------------------------------------------------------------
# do_fill (auto_submit=False invariant)
# ---------------------------------------------------------------------------


class TestDoFill:
    def test_fill_never_auto_submits(self):
        """fill_application_form must always be called with auto_submit=False."""
        item = _make_item("item1", "offer1")

        with (
            patch("cv_sender.rapid_apply_service.get_queue_item_by_id", return_value=item),
            patch("cv_sender.rapid_apply_service.mark_queue_item_status"),
            patch("cv_sender.services.fill_application_form") as mock_fill,
            patch("cv_sender.services._find_application_for_offer", return_value=None),
        ):
            mock_fill.return_value = _make_fill_result()

            from cv_sender.rapid_apply_service import do_fill  # noqa: PLC0415

            do_fill("item1")

            call_kwargs = mock_fill.call_args
            assert call_kwargs.kwargs.get("auto_submit") is False

    def test_failed_fill_status_is_retryable(self, tmp_path):
        """A FAILED queue item must remain retryable (not sent to terminal)."""
        item = _make_item("item1", "offer1")
        fail_result = _make_fill_result(status=FillStatus.FAILED)

        with (
            patch("cv_sender.rapid_apply_service.get_queue_item_by_id", return_value=item),
            patch("cv_sender.rapid_apply_service.mark_queue_item_status") as mock_mark,
            patch("cv_sender.services.fill_application_form", return_value=fail_result),
            patch("cv_sender.services._find_application_for_offer", return_value=None),
        ):
            from cv_sender.rapid_apply_service import do_fill  # noqa: PLC0415

            do_fill("item1")

        # Last call to mark_queue_item_status should set FAILED (not SENT/SKIPPED)
        calls = mock_mark.call_args_list
        last_status = calls[-1].args[1]
        assert last_status == ApplyQueueItemStatus.FAILED

    def test_filled_result_sets_filled_status(self):
        item = _make_item("item1", "offer1")
        ok_result = _make_fill_result(status=FillStatus.FILLED)

        with (
            patch("cv_sender.rapid_apply_service.get_queue_item_by_id", return_value=item),
            patch("cv_sender.rapid_apply_service.mark_queue_item_status") as mock_mark,
            patch("cv_sender.services.fill_application_form", return_value=ok_result),
            patch("cv_sender.services._find_application_for_offer", return_value=None),
        ):
            from cv_sender.rapid_apply_service import do_fill  # noqa: PLC0415

            do_fill("item1")

        calls = mock_mark.call_args_list
        last_status = calls[-1].args[1]
        assert last_status == ApplyQueueItemStatus.FILLED


# ---------------------------------------------------------------------------
# get_session_stats
# ---------------------------------------------------------------------------


class TestGetSessionStats:
    def test_returns_session_stats_object(self):
        with patch("cv_sender.rapid_apply_service.get_queue_stats") as mock_stats, \
             patch("cv_sender.rapid_apply_service.get_active_queue_items", return_value=[]):
            mock_stats.return_value = {
                "queued": 5,
                "filled": 2,
                "sent": 3,
                "skipped": 1,
                "failed": 0,
                "in_progress": 1,
            }
            stats = get_session_stats()
        assert stats.queued == 5
        assert stats.sent == 3
        assert isinstance(stats, SessionStats)


# ---------------------------------------------------------------------------
# SKIP_REASONS constant
# ---------------------------------------------------------------------------


class TestSkipReasons:
    def test_skip_reasons_is_non_empty_list(self):
        assert len(SKIP_REASONS) > 0

    def test_skip_reasons_includes_low_salary(self):
        assert "low salary" in SKIP_REASONS

    def test_skip_reasons_includes_login_required(self):
        assert "login required" in SKIP_REASONS
