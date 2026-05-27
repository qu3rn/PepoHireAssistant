"""Apply queue — building and managing the rapid-apply list."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from cv_sender.models import (
    Application,
    ApplicationStatus,
    ApplyQueueItem,
    ApplyQueueItemStatus,
    Decision,
    Offer,
)
from cv_sender.storage import (
    add_to_apply_queue,
    get_queue_item_by_id,
    load_apply_queue,
    load_applications,
    load_offers,
    save_apply_queue,
    update_queue_item,
)

logger = logging.getLogger(__name__)

# Statuses that mean the application is already acted on
_SENT_STATUSES = {
    ApplicationStatus.SENT,
    ApplicationStatus.FOLLOW_UP_DUE,
    ApplicationStatus.FOLLOW_UP_SENT,
    ApplicationStatus.REPLY_RECEIVED,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.OFFER,
    ApplicationStatus.REJECTED,
    ApplicationStatus.NO_RESPONSE,
    ApplicationStatus.ARCHIVED,
}

# Source priority bonuses
_SOURCE_BONUS: dict[str, float] = {
    "justjoin": 5.0,
    "rocketjobs": 3.0,
    "nofluffjobs": 3.0,
    "pracuj": 2.0,
    "linkedin": 2.0,
    "manual": 0.0,
    "generic": 0.0,
}


def _priority_score(offer: Offer) -> float:
    base = float(offer.score or 0)
    bonus = _SOURCE_BONUS.get(offer.source, 0.0)
    return base + bonus


def build_apply_queue_from_offers(
    offers: list[Offer] | None = None,
    applications: list[Application] | None = None,
    queue_path: Path | None = None,
) -> list[ApplyQueueItem]:
    """Build a prioritised apply queue from all scored offers.

    Rules:
    - Include only offers with ``decision == APPLY`` or ``MAYBE``
    - Skip offers already sent (application exists with a sent-type status)
    - Skip offers already in the queue in a non-terminal state
    - Sort descending by priority_score
    """
    if offers is None:
        offers = load_offers()
    if applications is None:
        applications = load_applications()

    # Build a set of offer_ids that have been sent
    sent_offer_ids: set[str] = set()
    for app in applications:
        if app.status in _SENT_STATUSES:
            sent_offer_ids.add(app.offer_id)

    # Existing active queue entries
    existing_queue = load_apply_queue(queue_path)
    terminal = {ApplyQueueItemStatus.SENT, ApplyQueueItemStatus.SKIPPED, ApplyQueueItemStatus.FAILED}
    queued_offer_ids = {q.offer_id for q in existing_queue if q.status not in terminal}

    items: list[ApplyQueueItem] = []
    for offer in offers:
        if offer.decision not in (Decision.APPLY, Decision.MAYBE):
            continue
        if offer.id in sent_offer_ids:
            continue
        if offer.id in queued_offer_ids:
            continue

        items.append(
            ApplyQueueItem(
                offer_id=offer.id,
                company=offer.company,
                title=offer.title,
                source=offer.source,
                url=offer.url,
                score=offer.score,
                priority_score=_priority_score(offer),
                reasons=offer.decision_reasons or [],
                warnings=offer.extraction_warnings or [],
            )
        )

    items.sort(key=lambda x: x.priority_score, reverse=True)

    # Merge: keep existing active items at the top of their current position
    # and append new ones after
    active_existing = [q for q in existing_queue if q.status not in terminal]
    terminal_existing = [q for q in existing_queue if q.status in terminal]

    # Add only new items that are not already in queue
    for item in items:
        add_to_apply_queue(item, queue_path)

    # Reload and return
    return load_apply_queue(queue_path)


def get_next_queue_item(queue_path: Path | None = None) -> ApplyQueueItem | None:
    """Return the highest-priority QUEUED item, or ``None`` if the queue is empty."""
    queue = load_apply_queue(queue_path)
    for item in sorted(queue, key=lambda x: x.priority_score, reverse=True):
        if item.status == ApplyQueueItemStatus.QUEUED:
            return item
    return None


def mark_queue_item_status(
    item_id: str,
    status: ApplyQueueItemStatus,
    queue_path: Path | None = None,
) -> ApplyQueueItem | None:
    """Update the status of a queue item by id."""
    item = get_queue_item_by_id(item_id, queue_path)
    if item is None:
        logger.warning("Queue item %s not found", item_id)
        return None
    item = item.model_copy(update={"status": status, "updated_at": datetime.now(UTC)})
    update_queue_item(item, queue_path)
    return item


def remove_from_queue(item_id: str, queue_path: Path | None = None) -> bool:
    """Permanently delete a queue item.

    Returns ``True`` if the item was found and removed.
    """
    queue = load_apply_queue(queue_path)
    new_queue = [q for q in queue if q.id != item_id]
    if len(new_queue) == len(queue):
        return False
    save_apply_queue(new_queue, queue_path)
    return True


def get_queue_stats(queue_path: Path | None = None) -> dict[str, int]:
    """Return a count by status."""
    queue = load_apply_queue(queue_path)
    stats: dict[str, int] = {}
    for item in queue:
        stats[item.status] = stats.get(item.status, 0) + 1
    return stats


# ---------------------------------------------------------------------------
# Session helpers for Rapid Apply
# ---------------------------------------------------------------------------

# Statuses considered "active" / processable in a session
_ACTIVE_STATUSES = {
    ApplyQueueItemStatus.QUEUED,
    ApplyQueueItemStatus.IN_PROGRESS,
    ApplyQueueItemStatus.FILLED,
    ApplyQueueItemStatus.FAILED,
}

_TERMINAL_STATUSES = {
    ApplyQueueItemStatus.SENT,
    ApplyQueueItemStatus.SKIPPED,
}


def get_active_queue_items(
    queue_path: Path | None = None,
    *,
    min_score: int | None = None,
    source_filter: str | None = None,
    exclude_failed: bool = False,
) -> list[ApplyQueueItem]:
    """Return active queue items sorted by priority_score descending.

    Active = QUEUED, IN_PROGRESS, FILLED, or FAILED (retryable).
    Optionally filter by minimum score, source, or exclude failed.
    """
    queue = load_apply_queue(queue_path)
    active = []
    for item in queue:
        if item.status not in _ACTIVE_STATUSES:
            continue
        if exclude_failed and item.status == ApplyQueueItemStatus.FAILED:
            continue
        if min_score is not None and (item.score or 0) < min_score:
            continue
        if source_filter and item.source != source_filter:
            continue
        active.append(item)
    return sorted(active, key=lambda x: x.priority_score, reverse=True)


def get_queue_item_by_offer_id(
    offer_id: str,
    queue_path: Path | None = None,
) -> ApplyQueueItem | None:
    """Return the active (non-terminal) queue item for *offer_id*, or ``None``."""
    queue = load_apply_queue(queue_path)
    matches = [q for q in queue if q.offer_id == offer_id and q.status not in _TERMINAL_STATUSES]
    return matches[0] if matches else None


def advance_session(
    current_item_id: str,
    queue_path: Path | None = None,
    *,
    min_score: int | None = None,
    source_filter: str | None = None,
    exclude_failed: bool = False,
) -> ApplyQueueItem | None:
    """Return the next active item after *current_item_id*.

    Does not change any status.  Returns ``None`` when the queue is exhausted.
    """
    items = get_active_queue_items(
        queue_path,
        min_score=min_score,
        source_filter=source_filter,
        exclude_failed=exclude_failed,
    )
    ids = [i.id for i in items]
    try:
        idx = ids.index(current_item_id)
    except ValueError:
        # Current item was removed or moved to terminal — return first available
        return items[0] if items else None
    next_idx = idx + 1
    return items[next_idx] if next_idx < len(items) else None
