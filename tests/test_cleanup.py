"""Tests for cv_sender.cleanup — offer bulk-delete and data cleanup service."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cv_sender.cleanup import (
    BackupResult,
    BulkDeleteResult,
    DeleteResult,
    OfferDeleteFilters,
    RelatedCleanupOptions,
    clear_apply_queue,
    clear_collection_diagnostics,
    create_data_backup,
    delete_all_offers,
    delete_offer,
    delete_offers,
    delete_offers_by_filter,
    is_dev_offer,
    preview_offers_by_filter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_offer(
    offer_id: str,
    title: str = "React Developer",
    company: str = "ACME Corp",
    source: str = "nofluffjobs",
    decision: str = "apply",
    score: int = 75,
    created_at: str | None = None,
    url: str | None = None,
) -> dict:
    return {
        "id": offer_id,
        "title": title,
        "company": company,
        "source": source,
        "decision": decision,
        "score": score,
        "url": url or f"https://nofluffjobs.com/offer/{offer_id}",
        "created_at": created_at or datetime.now(UTC).isoformat(),
    }


def _make_queue_item(item_id: str, offer_id: str) -> dict:
    return {"id": item_id, "offer_id": offer_id, "status": "queued"}


def _make_application(app_id: str, offer_id: str) -> dict:
    return {"id": app_id, "offer_id": offer_id, "status": "new"}


def _write_json(path: Path, data: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _read_json(path: Path) -> list:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect all cleanup module paths to a temp directory."""
    import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

    monkeypatch.setattr(cleanup_mod, "_DEFAULT_OFFERS", tmp_path / "offers.json")
    monkeypatch.setattr(cleanup_mod, "_DEFAULT_APPLICATIONS", tmp_path / "applications.json")
    monkeypatch.setattr(cleanup_mod, "_DEFAULT_APPLY_QUEUE", tmp_path / "apply_queue.json")
    monkeypatch.setattr(cleanup_mod, "_DEFAULT_CAMPAIGNS", tmp_path / "campaigns.json")
    monkeypatch.setattr(cleanup_mod, "_DEFAULT_CAMPAIGN_ACTIVITIES", tmp_path / "campaign_activities.json")
    monkeypatch.setattr(cleanup_mod, "_DEFAULT_DIAGNOSTICS", tmp_path / "collection_diagnostics.json")
    monkeypatch.setattr(cleanup_mod, "_DEFAULT_DEBUG_DIR", tmp_path / "debug")
    monkeypatch.setattr(cleanup_mod, "_DEFAULT_BACKUP_ROOT", tmp_path / "backups")
    return tmp_path


@pytest.fixture()
def offers_with_queue(data_dir: Path) -> dict:
    """Write 3 offers and 3 matching queue items to temp storage."""
    import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

    offers = [
        _make_offer("o1", title="React Dev", source="nofluffjobs"),
        _make_offer("o2", title="Test Offer", source="dev"),
        _make_offer("o3", title="Frontend Lead", source="justjoin"),
    ]
    queue = [
        _make_queue_item("q1", "o1"),
        _make_queue_item("q2", "o2"),
        _make_queue_item("q3", "o3"),
    ]
    apps = [
        _make_application("a1", "o1"),
        _make_application("a2", "o2"),
    ]
    _write_json(cleanup_mod._DEFAULT_OFFERS, offers)
    _write_json(cleanup_mod._DEFAULT_APPLY_QUEUE, queue)
    _write_json(cleanup_mod._DEFAULT_APPLICATIONS, apps)
    return {"offers": offers, "queue": queue, "apps": apps}


# ---------------------------------------------------------------------------
# is_dev_offer
# ---------------------------------------------------------------------------


class TestIsDevOffer:
    def test_dev_source(self):
        assert is_dev_offer({"source": "dev", "title": "Anything", "url": "https://real.com"})

    def test_test_source(self):
        assert is_dev_offer({"source": "test", "title": "Anything", "url": "https://real.com"})

    def test_example_source(self):
        assert is_dev_offer({"source": "example", "title": "Anything", "url": "https://real.com"})

    def test_test_in_title(self):
        assert is_dev_offer({"source": "manual", "title": "Test Offer", "url": "https://real.com"})

    def test_demo_in_company(self):
        assert is_dev_offer({"source": "manual", "title": "Dev", "company": "demo corp", "url": "https://real.com"})

    def test_example_com_url(self):
        assert is_dev_offer({"source": "manual", "title": "React Dev", "url": "https://example.com/job"})

    def test_localhost_url(self):
        assert is_dev_offer({"source": "manual", "title": "React Dev", "url": "http://localhost:3000/job"})

    def test_real_offer_not_dev(self):
        assert not is_dev_offer({
            "source": "nofluffjobs",
            "title": "Senior React Developer",
            "company": "ACME Corp",
            "url": "https://nofluffjobs.com/job/abc",
        })

    def test_empty_offer(self):
        assert not is_dev_offer({})


# ---------------------------------------------------------------------------
# create_data_backup
# ---------------------------------------------------------------------------


class TestCreateDataBackup:
    def test_creates_backup_directory(self, data_dir: Path):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        _write_json(cleanup_mod._DEFAULT_OFFERS, [_make_offer("o1")])
        result = create_data_backup(reason="test_backup", operation="test")

        assert result.success
        assert Path(result.path).exists()
        assert "test_backup" in result.path

    def test_copies_existing_files(self, data_dir: Path):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        _write_json(cleanup_mod._DEFAULT_OFFERS, [_make_offer("o1")])
        _write_json(cleanup_mod._DEFAULT_APPLY_QUEUE, [_make_queue_item("q1", "o1")])

        result = create_data_backup(reason="copy_test")

        backup_dir = Path(result.path)
        assert (backup_dir / "offers.json").exists()
        assert (backup_dir / "apply_queue.json").exists()
        assert "offers.json" in result.files_copied

    def test_writes_metadata(self, data_dir: Path):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        result = create_data_backup(reason="meta_test", operation="delete_offers")
        meta = json.loads((Path(result.path) / "metadata.json").read_text())

        assert meta["reason"] == "meta_test"
        assert meta["operation"] == "delete_offers"
        assert "timestamp" in meta

    def test_empty_data_dir_succeeds(self, data_dir: Path):
        result = create_data_backup(reason="empty_test")
        assert result.success
        assert result.files_copied == []


# ---------------------------------------------------------------------------
# delete_offer (single)
# ---------------------------------------------------------------------------


class TestDeleteOffer:
    def test_deletes_existing_offer(self, data_dir: Path, offers_with_queue: dict):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        result = delete_offer("o1", create_backup=False)

        assert result.status == "deleted"
        remaining = _read_json(cleanup_mod._DEFAULT_OFFERS)
        assert not any(o["id"] == "o1" for o in remaining)

    def test_returns_not_found_for_missing_offer(self, data_dir: Path, offers_with_queue: dict):
        result = delete_offer("nonexistent", create_backup=False)
        assert result.status == "not_found"

    def test_does_not_delete_queue_items_when_option_off(self, data_dir: Path, offers_with_queue: dict):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        opts = RelatedCleanupOptions(delete_queue_items=False)
        delete_offer("o1", options=opts, create_backup=False)

        queue = _read_json(cleanup_mod._DEFAULT_APPLY_QUEUE)
        assert any(q["offer_id"] == "o1" for q in queue)

    def test_deletes_queue_items_by_default(self, data_dir: Path, offers_with_queue: dict):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        opts = RelatedCleanupOptions(delete_queue_items=True)
        result = delete_offer("o1", options=opts, create_backup=False)

        assert result.related_deleted >= 1
        queue = _read_json(cleanup_mod._DEFAULT_APPLY_QUEUE)
        assert not any(q["offer_id"] == "o1" for q in queue)

    def test_does_not_delete_applications_by_default(self, data_dir: Path, offers_with_queue: dict):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        delete_offer("o1", create_backup=False)

        apps = _read_json(cleanup_mod._DEFAULT_APPLICATIONS)
        assert any(a["offer_id"] == "o1" for a in apps)

    def test_deletes_applications_when_option_enabled(self, data_dir: Path, offers_with_queue: dict):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        opts = RelatedCleanupOptions(delete_applications=True)
        delete_offer("o1", options=opts, create_backup=False)

        apps = _read_json(cleanup_mod._DEFAULT_APPLICATIONS)
        assert not any(a["offer_id"] == "o1" for a in apps)

    def test_backup_failure_prevents_delete(self, data_dir: Path, monkeypatch: pytest.MonkeyPatch, offers_with_queue: dict):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        monkeypatch.setattr(
            cleanup_mod,
            "create_data_backup",
            lambda **kw: BackupResult(success=False, error="disk full"),
        )
        result = delete_offer("o1", create_backup=True)
        assert result.status == "failed"
        assert "disk full" in result.error
        # Offer should still exist
        remaining = _read_json(cleanup_mod._DEFAULT_OFFERS)
        assert any(o["id"] == "o1" for o in remaining)


# ---------------------------------------------------------------------------
# delete_offers (bulk)
# ---------------------------------------------------------------------------


class TestDeleteOffers:
    def test_deletes_multiple_offers(self, data_dir: Path, offers_with_queue: dict):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        result = delete_offers(["o1", "o3"], create_backup=False)

        assert result.deleted_count == 2
        assert result.not_found_count == 0
        remaining = _read_json(cleanup_mod._DEFAULT_OFFERS)
        assert len(remaining) == 1
        assert remaining[0]["id"] == "o2"

    def test_not_found_counted_separately(self, data_dir: Path, offers_with_queue: dict):
        result = delete_offers(["o1", "MISSING"], create_backup=False)

        assert result.deleted_count == 1
        assert result.not_found_count == 1

    def test_empty_list_returns_early(self, data_dir: Path, offers_with_queue: dict):
        result = delete_offers([], create_backup=False)
        assert result.deleted_count == 0
        assert result.requested_count == 0

    def test_creates_backup_by_default(self, data_dir: Path, offers_with_queue: dict):
        result = delete_offers(["o1"], create_backup=True, backup_reason="test")
        assert result.backup_path
        assert Path(result.backup_path).exists()

    def test_does_not_delete_applications_by_default(self, data_dir: Path, offers_with_queue: dict):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        delete_offers(["o1", "o2"], create_backup=False)

        apps = _read_json(cleanup_mod._DEFAULT_APPLICATIONS)
        assert len(apps) == 2  # untouched

    def test_deletes_queue_items_by_default(self, data_dir: Path, offers_with_queue: dict):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        delete_offers(["o1", "o2"], create_backup=False)

        queue = _read_json(cleanup_mod._DEFAULT_APPLY_QUEUE)
        assert not any(q["offer_id"] in {"o1", "o2"} for q in queue)
        # o3 queue item survives
        assert any(q["offer_id"] == "o3" for q in queue)

    def test_delete_applications_when_opted_in(self, data_dir: Path, offers_with_queue: dict):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        opts = RelatedCleanupOptions(delete_applications=True)
        delete_offers(["o1", "o2"], options=opts, create_backup=False)

        apps = _read_json(cleanup_mod._DEFAULT_APPLICATIONS)
        assert len(apps) == 0

    def test_backup_failure_prevents_delete(self, data_dir: Path, monkeypatch: pytest.MonkeyPatch, offers_with_queue: dict):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        monkeypatch.setattr(
            cleanup_mod,
            "create_data_backup",
            lambda **kw: BackupResult(success=False, error="no space"),
        )
        result = delete_offers(["o1"], create_backup=True)
        assert result.deleted_count == 0
        assert result.errors


# ---------------------------------------------------------------------------
# delete_all_offers
# ---------------------------------------------------------------------------


class TestDeleteAllOffers:
    def test_deletes_all(self, data_dir: Path, offers_with_queue: dict):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        result = delete_all_offers(create_backup=False)

        assert result.deleted_count == 3
        remaining = _read_json(cleanup_mod._DEFAULT_OFFERS)
        assert remaining == []

    def test_empty_storage_no_error(self, data_dir: Path):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        _write_json(cleanup_mod._DEFAULT_OFFERS, [])
        result = delete_all_offers(create_backup=False)
        assert result.deleted_count == 0
        assert not result.errors


# ---------------------------------------------------------------------------
# preview_offers_by_filter / delete_offers_by_filter
# ---------------------------------------------------------------------------


class TestFilterDelete:
    def test_filter_by_source(self, data_dir: Path, offers_with_queue: dict):
        filters = OfferDeleteFilters(source="nofluffjobs")
        matches = preview_offers_by_filter(filters)
        assert len(matches) == 1
        assert matches[0]["id"] == "o1"

    def test_filter_dev_only(self, data_dir: Path, offers_with_queue: dict):
        filters = OfferDeleteFilters(dev_only=True)
        matches = preview_offers_by_filter(filters)
        # o2 has source="dev" → is dev; o1 url has example.com → also dev
        dev_ids = {m["id"] for m in matches}
        assert "o2" in dev_ids

    def test_filter_score_below(self, data_dir: Path):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        offers = [
            _make_offer("o1", score=40),
            _make_offer("o2", score=80),
            _make_offer("o3", score=None),  # type: ignore[arg-type]
        ]
        _write_json(cleanup_mod._DEFAULT_OFFERS, offers)

        filters = OfferDeleteFilters(score_below=50)
        matches = preview_offers_by_filter(filters)
        ids = {m["id"] for m in matches}
        # o1 (40 < 50) and o3 (None = unscored, included) should match
        assert "o1" in ids
        assert "o2" not in ids
        assert "o3" in ids

    def test_filter_search_text(self, data_dir: Path, offers_with_queue: dict):
        filters = OfferDeleteFilters(search_text="frontend lead")
        matches = preview_offers_by_filter(filters)
        assert any(m["id"] == "o3" for m in matches)
        assert not any(m["id"] == "o1" for m in matches)

    def test_filter_created_before(self, data_dir: Path):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        old_date = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        new_date = datetime.now(UTC).isoformat()
        offers = [
            _make_offer("old", created_at=old_date),
            _make_offer("new", created_at=new_date),
        ]
        _write_json(cleanup_mod._DEFAULT_OFFERS, offers)

        cutoff = datetime.now(UTC) - timedelta(days=5)
        filters = OfferDeleteFilters(max_created_at=cutoff)
        matches = preview_offers_by_filter(filters)
        ids = {m["id"] for m in matches}
        assert "old" in ids
        assert "new" not in ids

    def test_delete_by_source_removes_matching_only(self, data_dir: Path, offers_with_queue: dict):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        filters = OfferDeleteFilters(source="justjoin")
        result = delete_offers_by_filter(filters, create_backup=False)

        assert result.deleted_count == 1
        remaining = _read_json(cleanup_mod._DEFAULT_OFFERS)
        assert not any(o["id"] == "o3" for o in remaining)
        assert any(o["id"] == "o1" for o in remaining)

    def test_no_matches_returns_zero_deleted(self, data_dir: Path, offers_with_queue: dict):
        filters = OfferDeleteFilters(source="linkedin")
        result = delete_offers_by_filter(filters, create_backup=False)
        assert result.deleted_count == 0


# ---------------------------------------------------------------------------
# clear_apply_queue
# ---------------------------------------------------------------------------


class TestClearApplyQueue:
    def test_clears_all_items(self, data_dir: Path, offers_with_queue: dict):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        result = clear_apply_queue(create_backup=False)

        assert result.deleted_count == 3
        remaining = _read_json(cleanup_mod._DEFAULT_APPLY_QUEUE)
        assert remaining == []

    def test_missing_file_no_error(self, data_dir: Path):
        # No queue file written — should handle gracefully
        result = clear_apply_queue(create_backup=False)
        assert result.deleted_count == 0
        assert not result.errors


# ---------------------------------------------------------------------------
# clear_collection_diagnostics
# ---------------------------------------------------------------------------


class TestClearCollectionDiagnostics:
    def test_clears_diagnostics(self, data_dir: Path):
        import cv_sender.cleanup as cleanup_mod  # noqa: PLC0415

        _write_json(cleanup_mod._DEFAULT_DIAGNOSTICS, [{"run_id": "r1"}, {"run_id": "r2"}])
        result = clear_collection_diagnostics(create_backup=False)
        assert result.deleted_count == 2
        assert _read_json(cleanup_mod._DEFAULT_DIAGNOSTICS) == []
