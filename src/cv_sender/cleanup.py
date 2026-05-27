"""Offer bulk-delete and data cleanup service.

All destructive operations require the caller to explicitly pass the relevant
flags.  Nothing is deleted silently.

Typical usage
-------------
::

    from cv_sender.cleanup import delete_offers, OfferDeleteFilters, RelatedCleanupOptions

    result = delete_offers(
        offer_ids=["abc", "def"],
        options=RelatedCleanupOptions(delete_queue_items=True),
        create_backup=True,
        backup_reason="dev cleanup",
    )
    print(result.deleted_count, "offers deleted")
    print("Backup at:", result.backup_path)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DEFAULT_OFFERS = Path(os.getenv("OFFERS_PATH", "data/offers.json"))
_DEFAULT_APPLICATIONS = Path(os.getenv("APPLICATIONS_PATH", "data/applications.json"))
_DEFAULT_APPLY_QUEUE = Path(os.getenv("APPLY_QUEUE_PATH", "data/apply_queue.json"))
_DEFAULT_CAMPAIGNS = Path(os.getenv("CAMPAIGNS_PATH", "data/campaigns.json"))
_DEFAULT_CAMPAIGN_ACTIVITIES = Path(
    os.getenv("CAMPAIGN_ACTIVITIES_PATH", "data/campaign_activities.json")
)
_DEFAULT_DIAGNOSTICS = Path(
    os.getenv("COLLECTION_DIAGNOSTICS_PATH", "data/collection_diagnostics.json")
)
_DEFAULT_DEBUG_DIR = Path("data/debug")
_DEFAULT_BACKUP_ROOT = Path("data/backups")

"""Patterns that identify dev / test offers.

Title/company patterns are matched as whole words only (surrounded by word
boundaries) to avoid false positives like "developer" matching "dev".
"""

import re as _re

_DEV_TITLE_PATTERNS_RE = _re.compile(
    r"\b(test|dev|example|demo|fake|dummy)\b", _re.IGNORECASE
)
_DEV_URL_PATTERNS = ("example.com", "localhost", "127.0.0.1", "/test")
_DEV_SOURCES = {"dev", "test", "example"}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class DeleteResult(BaseModel):
    """Result for a single-offer delete."""

    id: str
    status: Literal["deleted", "not_found", "failed"]
    related_deleted: int = 0
    error: str = ""


class BulkDeleteResult(BaseModel):
    """Aggregated result of a bulk delete operation."""

    requested_count: int = 0
    deleted_count: int = 0
    not_found_count: int = 0
    failed_count: int = 0
    backup_path: str = ""
    deleted_offer_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class BackupResult(BaseModel):
    """Result of a backup operation."""

    success: bool
    path: str = ""
    error: str = ""
    files_copied: list[str] = Field(default_factory=list)


class OfferDeleteFilters(BaseModel):
    """Criteria for filter-based bulk deletion.

    All fields are optional — only non-None / non-empty fields are applied.
    Multiple filters are combined with AND logic (offer must match all).
    """

    source: str = ""
    decision: str = ""
    status: str = ""
    min_created_at: datetime | None = None
    max_created_at: datetime | None = None
    search_text: str = ""          # checked against title + company
    dev_only: bool = False         # heuristic dev/test detection
    score_below: int | None = None
    campaign_id: str = ""


class RelatedCleanupOptions(BaseModel):
    """Which related records to remove when an offer is deleted."""

    delete_queue_items: bool = True
    delete_quality_reports: bool = True
    delete_applications: bool = False   # Dangerous — off by default
    delete_debug_runs: bool = False


# ---------------------------------------------------------------------------
# Dev / test detection
# ---------------------------------------------------------------------------


def is_dev_offer(offer_data: dict) -> bool:
    """Return True if the offer looks like a dev/test artefact.

    Checks source, title, company, URL against known dev patterns.
    Conservative — only flags obvious cases.  Title/company patterns use
    whole-word matching to avoid false positives (e.g. "developer" ≠ "dev").
    """
    source = (offer_data.get("source") or "").lower()
    if source in _DEV_SOURCES:
        return True

    title = offer_data.get("title") or ""
    company = offer_data.get("company") or ""
    combined = title + " " + company
    if _DEV_TITLE_PATTERNS_RE.search(combined):
        return True

    url = (offer_data.get("url") or "").lower()
    if any(p in url for p in _DEV_URL_PATTERNS):
        return True

    return False


# ---------------------------------------------------------------------------
# Backup helpers
# ---------------------------------------------------------------------------


def create_data_backup(reason: str = "", operation: str = "", extra_meta: dict | None = None) -> BackupResult:
    """Copy all data JSON files into ``data/backups/YYYYMMDD_HHMMSS_<reason>/``.

    Returns a :class:`BackupResult` indicating success/failure and the path.
    """
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    slug = reason.lower().replace(" ", "_")[:30] if reason else "backup"
    backup_dir = _DEFAULT_BACKUP_ROOT / f"{ts}_{slug}"

    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return BackupResult(success=False, error=f"Cannot create backup dir: {exc}")

    files_to_backup = [
        _DEFAULT_OFFERS,
        _DEFAULT_APPLICATIONS,
        _DEFAULT_APPLY_QUEUE,
        _DEFAULT_CAMPAIGNS,
        _DEFAULT_CAMPAIGN_ACTIVITIES,
        _DEFAULT_DIAGNOSTICS,
    ]

    copied: list[str] = []
    for src in files_to_backup:
        if src.exists():
            dest = backup_dir / src.name
            try:
                shutil.copy2(src, dest)
                copied.append(src.name)
            except OSError as exc:
                logger.warning("Backup: could not copy %s: %s", src, exc)

    # Write metadata
    meta = {
        "timestamp": datetime.now(UTC).isoformat(),
        "reason": reason,
        "operation": operation,
        "files_copied": copied,
        **(extra_meta or {}),
    }
    try:
        (backup_dir / "metadata.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Backup: could not write metadata: %s", exc)

    logger.info("Backup created at %s (%d files)", backup_dir, len(copied))
    return BackupResult(success=True, path=str(backup_dir), files_copied=copied)


# ---------------------------------------------------------------------------
# Low-level JSON helpers (avoid circular import with storage.py)
# ---------------------------------------------------------------------------


def _read_json_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        try:
            data = json.load(fh)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []


def _write_json_list(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Core delete helpers
# ---------------------------------------------------------------------------


def _remove_queue_items_for_offers(offer_ids: set[str]) -> int:
    """Delete apply-queue items whose offer_id is in *offer_ids*. Returns count removed."""
    items = _read_json_list(_DEFAULT_APPLY_QUEUE)
    kept = [i for i in items if i.get("offer_id") not in offer_ids]
    removed = len(items) - len(kept)
    if removed > 0:
        _write_json_list(_DEFAULT_APPLY_QUEUE, kept)
    return removed


def _remove_applications_for_offers(offer_ids: set[str]) -> int:
    """Delete applications whose offer_id is in *offer_ids*. Returns count removed."""
    apps = _read_json_list(_DEFAULT_APPLICATIONS)
    kept = [a for a in apps if a.get("offer_id") not in offer_ids]
    removed = len(apps) - len(kept)
    if removed > 0:
        _write_json_list(_DEFAULT_APPLICATIONS, kept)
    return removed


def _detach_campaign_queue_refs(offer_ids: set[str]) -> int:
    """Remove offer references from campaign-activity records. Returns count touched."""
    activities = _read_json_list(_DEFAULT_CAMPAIGN_ACTIVITIES)
    touched = 0
    updated: list[dict] = []
    for act in activities:
        if act.get("offer_id") in offer_ids:
            # Detach: zero out the offer reference rather than deleting the activity
            act = {**act, "offer_id": ""}
            touched += 1
        updated.append(act)
    if touched > 0:
        _write_json_list(_DEFAULT_CAMPAIGN_ACTIVITIES, updated)
    return touched


def _remove_debug_runs_for_offers(offer_ids: set[str]) -> int:
    """Delete debug-run directories whose metadata links to a deleted offer."""
    removed = 0
    if not _DEFAULT_DEBUG_DIR.exists():
        return 0
    for run_dir in _DEFAULT_DEBUG_DIR.iterdir():
        if not run_dir.is_dir():
            continue
        meta_path = run_dir / "metadata.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("offer_id") in offer_ids:
                    shutil.rmtree(run_dir, ignore_errors=True)
                    removed += 1
            except (json.JSONDecodeError, OSError):
                continue
    return removed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def delete_offer(
    offer_id: str,
    options: RelatedCleanupOptions | None = None,
    create_backup: bool = False,
    backup_reason: str = "single_delete",
) -> DeleteResult:
    """Delete a single offer by id.

    Returns a :class:`DeleteResult` with status ``"deleted"`` / ``"not_found"`` /
    ``"failed"``.  Use *create_backup=True* to snapshot data before deletion.
    """
    opts = options or RelatedCleanupOptions()

    if create_backup:
        br = create_data_backup(reason=backup_reason, operation="delete_offer", extra_meta={"offer_ids": [offer_id]})
        if not br.success:
            return DeleteResult(id=offer_id, status="failed", error=f"Backup failed: {br.error}")

    offers = _read_json_list(_DEFAULT_OFFERS)
    original_count = len(offers)
    kept = [o for o in offers if o.get("id") != offer_id]

    if len(kept) == original_count:
        return DeleteResult(id=offer_id, status="not_found")

    try:
        _write_json_list(_DEFAULT_OFFERS, kept)
    except OSError as exc:
        return DeleteResult(id=offer_id, status="failed", error=str(exc))

    related = 0
    offer_set = {offer_id}
    if opts.delete_queue_items:
        related += _remove_queue_items_for_offers(offer_set)
    if opts.delete_applications:
        related += _remove_applications_for_offers(offer_set)
    if opts.delete_quality_reports:
        related += _detach_campaign_queue_refs(offer_set)
    if opts.delete_debug_runs:
        related += _remove_debug_runs_for_offers(offer_set)

    return DeleteResult(id=offer_id, status="deleted", related_deleted=related)


def delete_offers(
    offer_ids: list[str],
    options: RelatedCleanupOptions | None = None,
    create_backup: bool = True,
    backup_reason: str = "bulk_delete",
) -> BulkDeleteResult:
    """Delete a list of offers by id.

    Backup is created by default before any deletion.  Pass
    *create_backup=False* only when the caller has already taken a snapshot.
    """
    opts = options or RelatedCleanupOptions()
    result = BulkDeleteResult(requested_count=len(offer_ids))

    if not offer_ids:
        return result

    if create_backup:
        br = create_data_backup(
            reason=backup_reason,
            operation="delete_offers",
            extra_meta={"offer_ids": offer_ids, "count": len(offer_ids)},
        )
        if not br.success:
            result.errors.append(f"Backup failed: {br.error}")
            return result
        result.backup_path = br.path

    id_set = set(offer_ids)
    offers = _read_json_list(_DEFAULT_OFFERS)
    kept: list[dict] = []
    deleted_ids: list[str] = []

    for o in offers:
        oid = o.get("id", "")
        if oid in id_set:
            deleted_ids.append(oid)
        else:
            kept.append(o)

    found_ids = set(deleted_ids)
    for oid in offer_ids:
        if oid not in found_ids:
            result.not_found_count += 1

    try:
        _write_json_list(_DEFAULT_OFFERS, kept)
    except OSError as exc:
        result.errors.append(f"Write failed: {exc}")
        result.failed_count = len(offer_ids)
        return result

    result.deleted_count = len(deleted_ids)
    result.deleted_offer_ids = deleted_ids

    if opts.delete_queue_items:
        _remove_queue_items_for_offers(found_ids)
    if opts.delete_applications:
        _remove_applications_for_offers(found_ids)
    if opts.delete_quality_reports:
        _detach_campaign_queue_refs(found_ids)
    if opts.delete_debug_runs:
        _remove_debug_runs_for_offers(found_ids)

    return result


def delete_all_offers(
    options: RelatedCleanupOptions | None = None,
    create_backup: bool = True,
    backup_reason: str = "delete_all",
) -> BulkDeleteResult:
    """Delete every offer in storage.

    A backup is taken first unless *create_backup=False*.
    """
    opts = options or RelatedCleanupOptions()
    offers = _read_json_list(_DEFAULT_OFFERS)
    all_ids = [o.get("id", "") for o in offers if o.get("id")]
    return delete_offers(all_ids, options=opts, create_backup=create_backup, backup_reason=backup_reason)


def delete_offers_by_filter(
    filters: OfferDeleteFilters,
    options: RelatedCleanupOptions | None = None,
    create_backup: bool = True,
) -> BulkDeleteResult:
    """Delete offers that match *filters*.

    Use :func:`preview_offers_by_filter` first to see what will be removed.
    """
    matching = preview_offers_by_filter(filters)
    ids = [o["id"] for o in matching if o.get("id")]
    return delete_offers(ids, options=options, create_backup=create_backup, backup_reason="filter_delete")


def preview_offers_by_filter(filters: OfferDeleteFilters) -> list[dict]:
    """Return raw offer dicts that match *filters* without deleting anything."""
    offers = _read_json_list(_DEFAULT_OFFERS)
    return [o for o in offers if _matches_filter(o, filters)]


def _matches_filter(offer: dict, f: OfferDeleteFilters) -> bool:
    if f.source and (offer.get("source") or "") != f.source:
        return False

    if f.decision and (offer.get("decision") or "") != f.decision:
        return False

    if f.status and (offer.get("status") or "") != f.status:
        return False

    if f.search_text:
        needle = f.search_text.lower()
        haystack = (
            (offer.get("title") or "") + " " + (offer.get("company") or "")
        ).lower()
        if needle not in haystack:
            return False

    if f.min_created_at is not None:
        created = _parse_dt(offer.get("created_at"))
        if created is None or created < f.min_created_at:
            return False

    if f.max_created_at is not None:
        created = _parse_dt(offer.get("created_at"))
        if created is None or created > f.max_created_at:
            return False

    if f.score_below is not None:
        score = offer.get("score")
        # Only reject scored offers; unscored offers (score=None) are included
        if score is not None and int(score) >= f.score_below:
            return False

    if f.dev_only and not is_dev_offer(offer):
        return False

    if f.campaign_id:
        # Match offers referenced in campaign activities
        activities = _read_json_list(_DEFAULT_CAMPAIGN_ACTIVITIES)
        linked = {a.get("offer_id") for a in activities if a.get("campaign_id") == f.campaign_id}
        if offer.get("id") not in linked:
            return False

    return True


def _parse_dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Utility: clear auxiliary data files
# ---------------------------------------------------------------------------


def clear_apply_queue(create_backup: bool = True) -> BulkDeleteResult:
    """Truncate the apply-queue file."""
    result = BulkDeleteResult()
    if create_backup:
        br = create_data_backup(reason="clear_queue", operation="clear_apply_queue")
        if not br.success:
            result.errors.append(f"Backup failed: {br.error}")
            return result
        result.backup_path = br.path

    items = _read_json_list(_DEFAULT_APPLY_QUEUE)
    result.requested_count = len(items)
    try:
        _write_json_list(_DEFAULT_APPLY_QUEUE, [])
        result.deleted_count = len(items)
    except OSError as exc:
        result.errors.append(str(exc))
        result.failed_count = len(items)
    return result


def clear_collection_diagnostics(create_backup: bool = True) -> BulkDeleteResult:
    """Truncate the collection-diagnostics file."""
    result = BulkDeleteResult()
    if create_backup:
        br = create_data_backup(reason="clear_diagnostics", operation="clear_collection_diagnostics")
        if not br.success:
            result.errors.append(f"Backup failed: {br.error}")
            return result
        result.backup_path = br.path

    records = _read_json_list(_DEFAULT_DIAGNOSTICS)
    result.requested_count = len(records)
    try:
        _write_json_list(_DEFAULT_DIAGNOSTICS, [])
        result.deleted_count = len(records)
    except OSError as exc:
        result.errors.append(str(exc))
        result.failed_count = len(records)
    return result


def clear_debug_data(create_backup: bool = False) -> BulkDeleteResult:
    """Delete all form-filling debug run directories under ``data/debug/``."""
    result = BulkDeleteResult()
    if not _DEFAULT_DEBUG_DIR.exists():
        return result

    run_dirs = [d for d in _DEFAULT_DEBUG_DIR.iterdir() if d.is_dir()]
    result.requested_count = len(run_dirs)

    for run_dir in run_dirs:
        try:
            shutil.rmtree(run_dir)
            result.deleted_count += 1
        except OSError as exc:
            result.failed_count += 1
            result.errors.append(f"{run_dir.name}: {exc}")

    return result


def dev_cleanup(
    options: RelatedCleanupOptions | None = None,
    create_backup: bool = True,
) -> dict[str, BulkDeleteResult]:
    """Delete all dev/test offers, clear queue, diagnostics, and optionally debug data.

    Applications are NOT deleted unless *options.delete_applications=True*.
    Returns a dict of operation_name → BulkDeleteResult.
    """
    opts = options or RelatedCleanupOptions(delete_debug_runs=False, delete_applications=False)

    # One shared backup for the whole operation
    backup_path = ""
    if create_backup:
        br = create_data_backup(reason="dev_cleanup", operation="dev_cleanup")
        if not br.success:
            raise RuntimeError(f"Backup failed: {br.error}")
        backup_path = br.path

    # Delete dev offers
    dev_filters = OfferDeleteFilters(dev_only=True)
    offers_result = delete_offers_by_filter(dev_filters, options=opts, create_backup=False)
    offers_result.backup_path = backup_path

    queue_result = clear_apply_queue(create_backup=False)
    queue_result.backup_path = backup_path

    diag_result = clear_collection_diagnostics(create_backup=False)
    diag_result.backup_path = backup_path

    debug_result = BulkDeleteResult()
    if opts.delete_debug_runs:
        debug_result = clear_debug_data(create_backup=False)
        debug_result.backup_path = backup_path

    return {
        "offers": offers_result,
        "queue": queue_result,
        "diagnostics": diag_result,
        "debug": debug_result,
    }
