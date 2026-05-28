"""Tests for apply_queue module — queue building, ordering, status management."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cv_sender.collectors.base import JobSearchCriteria
from cv_sender.models import (
    Application,
    ApplicationStatus,
    ApplyQueueItem,
    ApplyQueueItemStatus,
    Decision,
    Offer,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_offer(
    offer_id: str = "o1",
    title: str = "React Developer",
    score: int = 80,
    decision: Decision = Decision.APPLY,
    source: str = "justjoin",
) -> Offer:
    return Offer(
        id=offer_id,
        url=f"https://example.com/{offer_id}",
        title=title,
        company="ACME",
        source=source,
        score=score,
        decision=decision,
    )


def _make_application(offer_id: str, status: ApplicationStatus) -> Application:
    from datetime import UTC, datetime  # noqa: PLC0415

    return Application(
        offer_id=offer_id,
        offer_url=f"https://example.com/{offer_id}",
        offer_title="React Developer",
        company="ACME",
        status=status,
        sent_at=datetime.now(UTC) if status != ApplicationStatus.NEW else None,
    )


# ---------------------------------------------------------------------------
# build_apply_queue_from_offers
# ---------------------------------------------------------------------------


class TestBuildApplyQueue:
    def test_apply_decision_included(self, tmp_path):
        offers = [_make_offer("o1", decision=Decision.APPLY)]
        result = _build_queue(offers, [], tmp_path)
        assert any(q.offer_id == "o1" for q in result)

    def test_maybe_decision_included(self, tmp_path):
        offers = [_make_offer("o1", decision=Decision.MAYBE)]
        result = _build_queue(offers, [], tmp_path)
        assert any(q.offer_id == "o1" for q in result)

    def test_skip_decision_excluded(self, tmp_path):
        offers = [_make_offer("o1", decision=Decision.SKIP)]
        result = _build_queue(offers, [], tmp_path)
        assert not any(q.offer_id == "o1" for q in result)

    def test_no_decision_excluded(self, tmp_path):
        offers = [_make_offer("o1", decision=None)]
        result = _build_queue(offers, [], tmp_path)
        assert not any(q.offer_id == "o1" for q in result)

    def test_already_sent_application_excluded(self, tmp_path):
        offers = [_make_offer("o1", decision=Decision.APPLY)]
        apps = [_make_application("o1", ApplicationStatus.SENT)]
        result = _build_queue(offers, apps, tmp_path)
        assert not any(q.offer_id == "o1" for q in result)

    def test_new_application_not_blocking(self, tmp_path):
        offers = [_make_offer("o1", decision=Decision.APPLY)]
        apps = [_make_application("o1", ApplicationStatus.NEW)]
        result = _build_queue(offers, apps, tmp_path)
        assert any(q.offer_id == "o1" for q in result)

    def test_sorted_by_priority_score_descending(self, tmp_path):
        offers = [
            _make_offer("low", score=40, source="pracuj"),
            _make_offer("high", score=90, source="justjoin"),
            _make_offer("mid", score=65, source="rocketjobs"),
        ]
        result = _build_queue(offers, [], tmp_path)
        scores = [q.priority_score for q in result]
        assert scores == sorted(scores, reverse=True)

    def test_justjoin_gets_bonus(self, tmp_path):
        offers = [
            _make_offer("jj", score=70, source="justjoin"),
            _make_offer("manual", score=70, source="manual"),
        ]
        result = _build_queue(offers, [], tmp_path)
        jj_item = next(q for q in result if q.offer_id == "jj")
        manual_item = next(q for q in result if q.offer_id == "manual")
        assert jj_item.priority_score > manual_item.priority_score

    def test_no_duplicates_in_queue(self, tmp_path):
        offers = [_make_offer("o1", decision=Decision.APPLY)]
        # First build
        _build_queue(offers, [], tmp_path)
        # Second build — same offer
        result = _build_queue(offers, [], tmp_path)
        offer_ids = [q.offer_id for q in result]
        assert offer_ids.count("o1") == 1

    def test_queued_status_default(self, tmp_path):
        offers = [_make_offer("o1")]
        result = _build_queue(offers, [], tmp_path)
        item = next(q for q in result if q.offer_id == "o1")
        assert item.status == ApplyQueueItemStatus.QUEUED

    def test_interview_application_excluded(self, tmp_path):
        offers = [_make_offer("o1", decision=Decision.APPLY)]
        apps = [_make_application("o1", ApplicationStatus.INTERVIEW)]
        result = _build_queue(offers, apps, tmp_path)
        assert not any(q.offer_id == "o1" for q in result)

    def test_rejected_application_excluded(self, tmp_path):
        offers = [_make_offer("o1", decision=Decision.APPLY)]
        apps = [_make_application("o1", ApplicationStatus.REJECTED)]
        result = _build_queue(offers, apps, tmp_path)
        assert not any(q.offer_id == "o1" for q in result)

    def test_multiple_offers_mixed_statuses(self, tmp_path):
        offers = [
            _make_offer("apply_new", decision=Decision.APPLY),
            _make_offer("maybe_sent", decision=Decision.MAYBE),
            _make_offer("skip_one", decision=Decision.SKIP),
        ]
        apps = [_make_application("maybe_sent", ApplicationStatus.SENT)]
        result = _build_queue(offers, apps, tmp_path)
        ids = [q.offer_id for q in result]
        assert "apply_new" in ids
        assert "maybe_sent" not in ids
        assert "skip_one" not in ids


def _build_queue(
    offers: list[Offer],
    applications: list[Application],
    tmp_path: Path,
) -> list[ApplyQueueItem]:
    from cv_sender.apply_queue import build_apply_queue_from_offers  # noqa: PLC0415

    queue_path = tmp_path / "queue.json"
    return build_apply_queue_from_offers(offers, applications, queue_path)


# ---------------------------------------------------------------------------
# get_next_queue_item
# ---------------------------------------------------------------------------


class TestGetNextQueueItem:
    def test_returns_highest_priority_queued(self, tmp_path):
        from cv_sender.apply_queue import get_next_queue_item  # noqa: PLC0415
        from cv_sender.storage import save_apply_queue  # noqa: PLC0415

        queue_path = tmp_path / "queue.json"
        items = [
            ApplyQueueItem(offer_id="low", priority_score=10.0, title="Low"),
            ApplyQueueItem(offer_id="high", priority_score=90.0, title="High"),
            ApplyQueueItem(offer_id="mid", priority_score=50.0, title="Mid"),
        ]
        save_apply_queue(items, queue_path)

        next_item = get_next_queue_item(queue_path)
        assert next_item is not None
        assert next_item.offer_id == "high"

    def test_skips_non_queued_statuses(self, tmp_path):
        from cv_sender.apply_queue import get_next_queue_item  # noqa: PLC0415
        from cv_sender.storage import save_apply_queue  # noqa: PLC0415

        queue_path = tmp_path / "queue.json"
        items = [
            ApplyQueueItem(
                offer_id="sent", priority_score=99.0, status=ApplyQueueItemStatus.SENT
            ),
            ApplyQueueItem(
                offer_id="queued", priority_score=10.0, status=ApplyQueueItemStatus.QUEUED
            ),
        ]
        save_apply_queue(items, queue_path)

        next_item = get_next_queue_item(queue_path)
        assert next_item is not None
        assert next_item.offer_id == "queued"

    def test_returns_none_when_empty(self, tmp_path):
        from cv_sender.apply_queue import get_next_queue_item  # noqa: PLC0415

        queue_path = tmp_path / "queue.json"
        assert get_next_queue_item(queue_path) is None

    def test_returns_none_when_all_done(self, tmp_path):
        from cv_sender.apply_queue import get_next_queue_item  # noqa: PLC0415
        from cv_sender.storage import save_apply_queue  # noqa: PLC0415

        queue_path = tmp_path / "queue.json"
        items = [
            ApplyQueueItem(offer_id="s", priority_score=80.0, status=ApplyQueueItemStatus.SENT),
            ApplyQueueItem(offer_id="sk", priority_score=70.0, status=ApplyQueueItemStatus.SKIPPED),
        ]
        save_apply_queue(items, queue_path)
        assert get_next_queue_item(queue_path) is None


# ---------------------------------------------------------------------------
# mark_queue_item_status
# ---------------------------------------------------------------------------


class TestMarkQueueItemStatus:
    def test_updates_status(self, tmp_path):
        from cv_sender.apply_queue import mark_queue_item_status  # noqa: PLC0415
        from cv_sender.storage import load_apply_queue, save_apply_queue  # noqa: PLC0415

        queue_path = tmp_path / "queue.json"
        item = ApplyQueueItem(offer_id="o1", priority_score=50.0)
        save_apply_queue([item], queue_path)

        updated = mark_queue_item_status(item.id, ApplyQueueItemStatus.SENT, queue_path)
        assert updated is not None
        assert updated.status == ApplyQueueItemStatus.SENT

        reloaded = load_apply_queue(queue_path)
        assert reloaded[0].status == ApplyQueueItemStatus.SENT

    def test_returns_none_for_unknown_id(self, tmp_path):
        from cv_sender.apply_queue import mark_queue_item_status  # noqa: PLC0415

        queue_path = tmp_path / "queue.json"
        result = mark_queue_item_status("nonexistent", ApplyQueueItemStatus.SENT, queue_path)
        assert result is None


# ---------------------------------------------------------------------------
# remove_from_queue
# ---------------------------------------------------------------------------


class TestRemoveFromQueue:
    def test_removes_existing_item(self, tmp_path):
        from cv_sender.apply_queue import remove_from_queue  # noqa: PLC0415
        from cv_sender.storage import load_apply_queue, save_apply_queue  # noqa: PLC0415

        queue_path = tmp_path / "queue.json"
        item = ApplyQueueItem(offer_id="o1", priority_score=50.0)
        save_apply_queue([item], queue_path)

        removed = remove_from_queue(item.id, queue_path)
        assert removed is True
        assert load_apply_queue(queue_path) == []

    def test_returns_false_for_unknown_id(self, tmp_path):
        from cv_sender.apply_queue import remove_from_queue  # noqa: PLC0415

        queue_path = tmp_path / "queue.json"
        assert remove_from_queue("no-such-id", queue_path) is False


# ---------------------------------------------------------------------------
# get_queue_stats
# ---------------------------------------------------------------------------


class TestGetQueueStats:
    def test_counts_by_status(self, tmp_path):
        from cv_sender.apply_queue import get_queue_stats  # noqa: PLC0415
        from cv_sender.storage import save_apply_queue  # noqa: PLC0415

        queue_path = tmp_path / "queue.json"
        items = [
            ApplyQueueItem(offer_id="a", status=ApplyQueueItemStatus.QUEUED, priority_score=1),
            ApplyQueueItem(offer_id="b", status=ApplyQueueItemStatus.QUEUED, priority_score=1),
            ApplyQueueItem(offer_id="c", status=ApplyQueueItemStatus.SENT, priority_score=1),
        ]
        save_apply_queue(items, queue_path)

        stats = get_queue_stats(queue_path)
        assert stats["queued"] == 2
        assert stats["sent"] == 1


# ---------------------------------------------------------------------------
# Storage: add_to_apply_queue (deduplication)
# ---------------------------------------------------------------------------


class TestAddToApplyQueue:
    def test_prevents_duplicate_active_offer(self, tmp_path):
        from cv_sender.storage import add_to_apply_queue, load_apply_queue  # noqa: PLC0415

        queue_path = tmp_path / "queue.json"
        item = ApplyQueueItem(offer_id="o1", priority_score=50.0)
        assert add_to_apply_queue(item, queue_path) is True

        duplicate = ApplyQueueItem(offer_id="o1", priority_score=50.0)
        assert add_to_apply_queue(duplicate, queue_path) is False
        assert len(load_apply_queue(queue_path)) == 1

    def test_allows_re_queue_after_terminal_status(self, tmp_path):
        from cv_sender.storage import add_to_apply_queue, load_apply_queue, save_apply_queue  # noqa: PLC0415

        queue_path = tmp_path / "queue.json"
        item = ApplyQueueItem(
            offer_id="o1",
            priority_score=50.0,
            status=ApplyQueueItemStatus.SENT,
        )
        save_apply_queue([item], queue_path)

        new_item = ApplyQueueItem(offer_id="o1", priority_score=50.0)
        assert add_to_apply_queue(new_item, queue_path) is True
        assert len(load_apply_queue(queue_path)) == 2


class TestQueueSync:
    def test_sync_queue_item_from_offer_refreshes_snapshot(self, tmp_path, monkeypatch):
        from cv_sender.apply_queue import sync_queue_item_from_offer  # noqa: PLC0415
        from cv_sender.storage import save_apply_queue, save_offers  # noqa: PLC0415

        import cv_sender.storage as _storage  # noqa: PLC0415

        offers_path = tmp_path / "offers.json"
        queue_path = tmp_path / "queue.json"
        monkeypatch.setattr(_storage, "_DEFAULT_OFFERS", offers_path)

        offer = Offer(
            id="offer-1",
            url="https://example.com/job/1",
            title="Clean Title",
            company="ACME Updated",
            source="manual",
            score=91,
            decision_reasons=["strong fit"],
            extraction_warnings=["verify location"],
        )
        save_offers([offer], path=offers_path)

        queue_item = ApplyQueueItem(
            offer_id=offer.id,
            title="old-slug-title",
            company="Old Company",
            source="manual",
            priority_score=1.0,
        )
        save_apply_queue([queue_item], path=queue_path)

        updated = sync_queue_item_from_offer(queue_item.id, queue_path)

        assert updated is not None
        assert updated.title == "Clean Title"
        assert updated.company == "ACME Updated"
        assert updated.score == 91
        assert "strong fit" in updated.reasons
        assert "verify location" in updated.warnings

    def test_sync_all_queue_items_from_offers_updates_multiple_rows(self, tmp_path, monkeypatch):
        from cv_sender.apply_queue import sync_all_queue_items_from_offers  # noqa: PLC0415
        from cv_sender.storage import save_apply_queue, save_offers  # noqa: PLC0415

        import cv_sender.storage as _storage  # noqa: PLC0415

        offers_path = tmp_path / "offers.json"
        queue_path = tmp_path / "queue.json"
        monkeypatch.setattr(_storage, "_DEFAULT_OFFERS", offers_path)
        monkeypatch.setattr(_storage, "_DEFAULT_APPLY_QUEUE", queue_path)

        offer_one = Offer(id="o1", url="https://example.com/1", title="One Clean", company="One Co", source="manual")
        offer_two = Offer(id="o2", url="https://example.com/2", title="Two Clean", company="Two Co", source="manual")
        save_offers([offer_one, offer_two], path=offers_path)

        queue_items = [
            ApplyQueueItem(offer_id="o1", title="one slug", company="old 1", source="manual", priority_score=1.0),
            ApplyQueueItem(offer_id="o2", title="two slug", company="old 2", source="manual", priority_score=1.0),
        ]
        save_apply_queue(queue_items, path=queue_path)

        changed = sync_all_queue_items_from_offers(queue_path)

        assert changed == 2


# ---------------------------------------------------------------------------
# Config: JobSearchConfig defaults
# ---------------------------------------------------------------------------


class TestJobSearchConfig:
    def test_default_sources_has_linkedin_disabled(self):
        from cv_sender.config import JobSearchConfig  # noqa: PLC0415

        cfg = JobSearchConfig()
        assert "linkedin" in cfg.sources
        assert cfg.sources["linkedin"].enabled is False

    def test_default_sources_has_justjoin_enabled(self):
        from cv_sender.config import JobSearchConfig  # noqa: PLC0415

        cfg = JobSearchConfig()
        assert cfg.sources["justjoin"].enabled is True

    def test_default_not_enabled(self):
        from cv_sender.config import JobSearchConfig  # noqa: PLC0415

        cfg = JobSearchConfig()
        assert cfg.enabled is False

    def test_load_settings_includes_job_search(self, tmp_path):
        from cv_sender.config import load_settings  # noqa: PLC0415

        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(
            "job_search:\n  enabled: true\n  min_salary_b2b: 20000\n",
            encoding="utf-8",
        )
        settings = load_settings(str(settings_file))
        assert settings.job_search.enabled is True
        assert settings.job_search.min_salary_b2b == 20000

    def test_load_settings_sources_parsed(self, tmp_path):
        from cv_sender.config import load_settings  # noqa: PLC0415

        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(
            "job_search:\n  sources:\n    justjoin:\n      enabled: false\n",
            encoding="utf-8",
        )
        settings = load_settings(str(settings_file))
        assert settings.job_search.sources["justjoin"].enabled is False
