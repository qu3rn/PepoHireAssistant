"""Rapid Apply session service — orchestrates fill → mark-sent → skip actions.

All UI button handlers should call these functions rather than touching
storage or apply_queue directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from cv_sender.apply_queue import (
    advance_session,
    get_active_queue_items,
    get_queue_stats,
    mark_queue_item_status,
)
from cv_sender.models import (
    ApplicationEvent,
    ApplicationStatus,
    ApplyQueueItem,
    ApplyQueueItemStatus,
    FillResult,
    FillStatus,
)
from cv_sender.storage import (
    get_queue_item_by_id,
    load_apply_queue,
    update_application,
)

logger = logging.getLogger(__name__)

# Reasons the user can give when skipping an offer
SKIP_REASONS = [
    "low salary",
    "poor fit",
    "duplicate",
    "login required",
    "broken form",
    "not interested",
    "other",
]


# ---------------------------------------------------------------------------
# Quality check (lightweight — no separate module required)
# ---------------------------------------------------------------------------


@dataclass
class QualityStatus:
    """Simple quality assessment of a fill result."""

    badge: Literal["ready", "review_needed", "not_ready"] = "not_ready"
    checklist: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def run_quality_check(result: FillResult) -> QualityStatus:
    """Assess a :class:`~cv_sender.models.FillResult` and return a :class:`QualityStatus`."""
    checklist: list[str] = []
    warnings: list[str] = []

    if result.status == FillStatus.FILLED:
        checklist.append("✅ Form filled successfully")
    elif result.status == FillStatus.PARTIAL:
        checklist.append("⚠️ Form partially filled")
        warnings.append("Some fields could not be filled automatically.")
    else:
        checklist.append("❌ Fill failed")
        warnings.append("The form could not be filled. Review the error and retry.")

    if result.fields_filled:
        checklist.append(f"✅ Fields filled: {', '.join(result.fields_filled)}")
    if result.fields_missing:
        checklist.append(f"⚠️ Fields missing: {', '.join(result.fields_missing)}")
        warnings.append(f"Missing fields require manual entry: {', '.join(result.fields_missing)}")
    if result.generated_answers:
        checklist.append(f"✅ Generated answers: {len(result.generated_answers)} question(s)")
    if result.warnings:
        for w in result.warnings:
            warnings.append(f"⚠️ {w}")

    if result.status == FillStatus.FAILED:
        badge: Literal["ready", "review_needed", "not_ready"] = "not_ready"
    elif result.fields_missing or result.warnings:
        badge = "review_needed"
    else:
        badge = "ready"

    return QualityStatus(badge=badge, checklist=checklist, warnings=warnings)


# ---------------------------------------------------------------------------
# Fill action
# ---------------------------------------------------------------------------


@dataclass
class FillActionResult:
    fill_result: FillResult
    quality: QualityStatus
    queue_item: ApplyQueueItem | None
    application_id: str


def do_fill(
    queue_item_id: str,
    *,
    selected_cv_id: str = "",
    force_generic: bool = False,
) -> FillActionResult:
    """Fill the application form for the given queue item.

    Updates queue item status to FILLED or FAILED.
    auto_submit is always False.
    """
    from cv_sender import services  # noqa: PLC0415

    item = get_queue_item_by_id(queue_item_id)
    if item is None:
        dummy = FillResult(
            status=FillStatus.FAILED,
            error=f"Queue item {queue_item_id!r} not found.",
        )
        return FillActionResult(
            fill_result=dummy,
            quality=run_quality_check(dummy),
            queue_item=None,
            application_id="",
        )

    # Mark in-progress
    mark_queue_item_status(item.id, ApplyQueueItemStatus.IN_PROGRESS)

    if force_generic:
        result = services.fill_application_form_retry(item.offer_id, force_generic=True)
    else:
        result = services.fill_application_form(
            item.offer_id,
            auto_submit=False,
            selected_cv_id=selected_cv_id,
        )

    # Update queue status
    new_status = (
        ApplyQueueItemStatus.FILLED
        if result.status != FillStatus.FAILED
        else ApplyQueueItemStatus.FAILED
    )
    updated_item = mark_queue_item_status(item.id, new_status)

    quality = run_quality_check(result)

    # Look up the application that was just created/updated
    app = services._find_application_for_offer(item.offer_id)  # noqa: SLF001
    app_id = app.id if app else ""

    return FillActionResult(
        fill_result=result,
        quality=quality,
        queue_item=updated_item,
        application_id=app_id,
    )


# ---------------------------------------------------------------------------
# Mark-sent action
# ---------------------------------------------------------------------------


@dataclass
class MarkSentResult:
    success: bool
    message: str
    next_item: ApplyQueueItem | None


def do_mark_sent(
    queue_item_id: str,
    *,
    min_score: int | None = None,
    source_filter: str | None = None,
    exclude_failed: bool = False,
) -> MarkSentResult:
    """Mark a queue item (and its linked application) as sent.

    Advances to the next active item automatically.
    """
    from cv_sender import services  # noqa: PLC0415

    item = get_queue_item_by_id(queue_item_id)
    if item is None:
        return MarkSentResult(
            success=False,
            message=f"Queue item {queue_item_id!r} not found.",
            next_item=None,
        )

    # Mark queue item
    mark_queue_item_status(item.id, ApplyQueueItemStatus.SENT)

    # Mark application
    app = services._find_application_for_offer(item.offer_id)  # noqa: SLF001
    msg = "Queue item marked as sent."
    if app:
        ok, detail = services.mark_application_sent(app.id)
        msg = detail if ok else f"Queue updated but application update failed: {detail}"

    next_item = advance_session(
        queue_item_id,
        min_score=min_score,
        source_filter=source_filter,
        exclude_failed=exclude_failed,
    )
    return MarkSentResult(success=True, message=msg, next_item=next_item)


# ---------------------------------------------------------------------------
# Skip action
# ---------------------------------------------------------------------------


@dataclass
class SkipResult:
    success: bool
    message: str
    next_item: ApplyQueueItem | None


def do_skip(
    queue_item_id: str,
    reason: str = "",
    *,
    min_score: int | None = None,
    source_filter: str | None = None,
    exclude_failed: bool = False,
) -> SkipResult:
    """Mark a queue item as skipped and advance to the next item.

    *reason* is stored as a queue item note and as an application event.
    """
    from cv_sender import services  # noqa: PLC0415
    from cv_sender.storage import get_queue_item_by_id as _get  # noqa: PLC0415

    item = get_queue_item_by_id(queue_item_id)
    if item is None:
        return SkipResult(
            success=False,
            message=f"Queue item {queue_item_id!r} not found.",
            next_item=None,
        )

    # Persist skip reason as a warning on the item (immutable model_copy)
    if reason:
        from cv_sender.storage import update_queue_item  # noqa: PLC0415

        updated = item.model_copy(
            update={
                "status": ApplyQueueItemStatus.SKIPPED,
                "warnings": list(item.warnings) + [f"Skipped: {reason}"],
                "updated_at": datetime.now(UTC),
            }
        )
        update_queue_item(updated)
    else:
        mark_queue_item_status(item.id, ApplyQueueItemStatus.SKIPPED)

    # Append event to application if one exists
    app = services._find_application_for_offer(item.offer_id)  # noqa: SLF001
    if app:
        now = datetime.now(UTC)
        app_updated = app.model_copy(
            update={"updated_at": now}
        )
        app_updated.events.append(
            ApplicationEvent(
                timestamp=now,
                event="skipped",
                details=f"Skipped in Rapid Apply session. Reason: {reason or 'none'}",
            )
        )
        update_application(app_updated)

    next_item = advance_session(
        queue_item_id,
        min_score=min_score,
        source_filter=source_filter,
        exclude_failed=exclude_failed,
    )
    reason_str = f" Reason: {reason}" if reason else ""
    return SkipResult(
        success=True,
        message=f"Skipped.{reason_str}",
        next_item=next_item,
    )


# ---------------------------------------------------------------------------
# Session stats
# ---------------------------------------------------------------------------


@dataclass
class SessionStats:
    queued: int = 0
    in_progress: int = 0
    filled: int = 0
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    total_active: int = 0


def get_session_stats() -> SessionStats:
    stats_raw = get_queue_stats()
    return SessionStats(
        queued=stats_raw.get(ApplyQueueItemStatus.QUEUED, 0),
        in_progress=stats_raw.get(ApplyQueueItemStatus.IN_PROGRESS, 0),
        filled=stats_raw.get(ApplyQueueItemStatus.FILLED, 0),
        sent=stats_raw.get(ApplyQueueItemStatus.SENT, 0),
        skipped=stats_raw.get(ApplyQueueItemStatus.SKIPPED, 0),
        failed=stats_raw.get(ApplyQueueItemStatus.FAILED, 0),
        total_active=len(get_active_queue_items()),
    )
