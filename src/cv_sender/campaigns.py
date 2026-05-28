"""Campaign service — create and manage focused job-application campaigns."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, date
from pathlib import Path

from cv_sender.models import (
    Campaign,
    CampaignActivity,
    CampaignActivityType,
    CampaignGoalType,
    CampaignStatus,
    ApplyQueueItemStatus,
)
from cv_sender.storage import (
    add_campaign,
    add_campaign_activity,
    get_campaign_activities,
    get_campaign_by_id,
    load_apply_queue,
    load_campaigns,
    update_campaign,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

REACT_SPRINT_PRESET: dict = {
    "name": "React Frontend Sprint",
    "target_count": 25,
    "goal_type": CampaignGoalType.APPLICATIONS_SENT,
    "keywords": ["React Developer", "Frontend Developer", "Frontend Engineer"],
    "technologies": ["React", "TypeScript", "Next.js"],
    "locations": ["Remote", "Poland"],
    "sources": ["justjoin", "rocketjobs", "nofluffjobs", "pracuj"],
    "collector_mode": "playwright",
    "min_score": 60,
    "include_maybe": True,
    "include_follow_ups": False,
}


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------


def create_campaign(
    name: str,
    *,
    target_count: int = 25,
    target_date: str = "",
    goal_type: CampaignGoalType = CampaignGoalType.APPLICATIONS_SENT,
    keywords: list[str] | None = None,
    technologies: list[str] | None = None,
    locations: list[str] | None = None,
    sources: list[str] | None = None,
    collector_mode: str = "",
    min_score: int = 0,
    min_salary_b2b: int = 0,
    require_salary: bool = False,
    include_follow_ups: bool = False,
    include_maybe: bool = True,
    notes: str = "",
) -> Campaign:
    """Create and persist a new campaign; returns the created :class:`Campaign`."""
    if not target_date:
        target_date = date.today().isoformat()

    campaign = Campaign(
        name=name,
        goal_type=goal_type,
        target_count=target_count,
        target_date=target_date,
        keywords=keywords or [],
        technologies=technologies or [],
        locations=locations or [],
        sources=sources or [],
        collector_mode=collector_mode,
        min_score=min_score,
        min_salary_b2b=min_salary_b2b,
        require_salary=require_salary,
        include_follow_ups=include_follow_ups,
        include_maybe=include_maybe,
        notes=notes,
        started_at=datetime.now(UTC),
    )
    add_campaign(campaign)
    logger.info("Created campaign %s (%s)", campaign.id, campaign.name)
    return campaign


def get_active_campaigns() -> list[Campaign]:
    """Return all campaigns with status ACTIVE."""
    return [c for c in load_campaigns() if c.status == CampaignStatus.ACTIVE]


def get_campaign(campaign_id: str) -> Campaign | None:
    """Return a campaign by id."""
    return get_campaign_by_id(campaign_id)


def resolve_campaign_collector_mode(campaign: Campaign, global_collector_mode: str | None = None) -> str:
    """Resolve effective collector mode for campaign collection actions."""
    if campaign.collector_mode:
        return campaign.collector_mode
    if global_collector_mode:
        return global_collector_mode
    try:
        from cv_sender.config import load_settings  # noqa: PLC0415

        return load_settings().job_search.collector_mode or "playwright"
    except Exception:  # noqa: BLE001
        return "playwright"


def update_campaign_status(
    campaign_id: str,
    status: CampaignStatus,
) -> Campaign | None:
    """Change the status of a campaign and persist it."""
    campaign = get_campaign_by_id(campaign_id)
    if campaign is None:
        logger.warning("Campaign %s not found", campaign_id)
        return None
    now = datetime.now(UTC)
    update_kwargs: dict = {"status": status, "updated_at": now}
    if status == CampaignStatus.COMPLETED and campaign.completed_at is None:
        update_kwargs["completed_at"] = now
    campaign = campaign.model_copy(update=update_kwargs)
    update_campaign(campaign)
    return campaign


# ---------------------------------------------------------------------------
# Activity recording
# ---------------------------------------------------------------------------


def record_campaign_activity(
    campaign_id: str,
    activity_type: CampaignActivityType,
    *,
    offer_id: str = "",
    application_id: str = "",
    queue_item_id: str = "",
    note: str = "",
) -> CampaignActivity:
    """Record a campaign activity and update the campaign counters."""
    activity = CampaignActivity(
        campaign_id=campaign_id,
        type=activity_type,
        offer_id=offer_id,
        application_id=application_id,
        queue_item_id=queue_item_id,
        note=note,
    )
    add_campaign_activity(activity)

    # Update denormalised counters on the campaign
    campaign = get_campaign_by_id(campaign_id)
    if campaign is not None:
        counter_map = {
            CampaignActivityType.SENT: "sent_count",
            CampaignActivityType.FILLED: "filled_count",
            CampaignActivityType.SKIPPED: "skipped_count",
            CampaignActivityType.FAILED: "failed_count",
            CampaignActivityType.FOLLOW_UP_SENT: "follow_up_count",
        }
        field_name = counter_map.get(activity_type)
        if field_name:
            new_val = getattr(campaign, field_name) + 1
            campaign = campaign.model_copy(
                update={field_name: new_val, "updated_at": datetime.now(UTC)}
            )
            update_campaign(campaign)

    return activity


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


@dataclass
class CampaignProgress:
    """Snapshot of how far a campaign has progressed."""

    campaign_id: str
    campaign_name: str
    target: int
    sent: int
    remaining: int
    filled_not_sent: int
    skipped: int
    failed: int
    follow_ups: int
    queued_available: int
    progress_pct: float
    queue_shortage: bool
    queue_shortage_message: str


def get_campaign_progress(campaign_id: str) -> CampaignProgress | None:
    """Compute campaign progress from activities and the current queue state."""
    campaign = get_campaign_by_id(campaign_id)
    if campaign is None:
        return None

    activities = get_campaign_activities(campaign_id)

    sent = sum(1 for a in activities if a.type == CampaignActivityType.SENT)
    filled = sum(1 for a in activities if a.type == CampaignActivityType.FILLED)
    skipped = sum(1 for a in activities if a.type == CampaignActivityType.SKIPPED)
    failed = sum(1 for a in activities if a.type == CampaignActivityType.FAILED)
    follow_ups = sum(
        1 for a in activities if a.type == CampaignActivityType.FOLLOW_UP_SENT
    )

    remaining = max(0, campaign.target_count - sent)

    # Count campaign queue items
    all_queue = load_apply_queue()
    campaign_queue = [q for q in all_queue if q.campaign_id == campaign_id]
    active_statuses = {
        ApplyQueueItemStatus.QUEUED,
        ApplyQueueItemStatus.IN_PROGRESS,
        ApplyQueueItemStatus.FILLED,
    }
    queued_available = sum(1 for q in campaign_queue if q.status in active_statuses)
    filled_not_sent = sum(
        1 for q in campaign_queue if q.status == ApplyQueueItemStatus.FILLED
    )

    progress_pct = min(100.0, round(sent / campaign.target_count * 100, 1)) if campaign.target_count > 0 else 0.0

    queue_shortage = queued_available < remaining
    queue_shortage_message = (
        f"Queue has only {queued_available} good offer(s) left. Collect more to reach target of {remaining} remaining."
        if queue_shortage and remaining > 0
        else ""
    )

    return CampaignProgress(
        campaign_id=campaign_id,
        campaign_name=campaign.name,
        target=campaign.target_count,
        sent=sent,
        remaining=remaining,
        filled_not_sent=filled_not_sent,
        skipped=skipped,
        failed=failed,
        follow_ups=follow_ups,
        queued_available=queued_available,
        progress_pct=progress_pct,
        queue_shortage=queue_shortage,
        queue_shortage_message=queue_shortage_message,
    )


# ---------------------------------------------------------------------------
# Queue attachment
# ---------------------------------------------------------------------------


def attach_queue_items_to_campaign(
    campaign_id: str,
    queue_item_ids: list[str],
) -> int:
    """Tag existing queue items with *campaign_id*.

    Returns the number of items successfully updated.
    """
    from cv_sender.storage import load_apply_queue, save_apply_queue  # noqa: PLC0415

    queue = load_apply_queue()
    id_set = set(queue_item_ids)
    updated_count = 0
    now = datetime.now(UTC)
    new_queue = []
    for item in queue:
        if item.id in id_set and item.campaign_id != campaign_id:
            item = item.model_copy(
                update={"campaign_id": campaign_id, "updated_at": now}
            )
            updated_count += 1
        new_queue.append(item)
    save_apply_queue(new_queue)
    return updated_count


def build_campaign_queue(
    campaign_id: str,
    *,
    min_score: int | None = None,
) -> list:
    """Attach unassigned active queue items that match the campaign criteria.

    Returns the list of newly-attached items.
    """
    from cv_sender.apply_queue import _ACTIVE_STATUSES  # noqa: PLC0415
    from cv_sender.storage import load_apply_queue  # noqa: PLC0415

    campaign = get_campaign_by_id(campaign_id)
    if campaign is None:
        return []

    effective_min_score = min_score if min_score is not None else campaign.min_score
    queue = load_apply_queue()

    candidate_ids = []
    for item in queue:
        if item.status not in _ACTIVE_STATUSES:
            continue
        if item.campaign_id:          # already claimed by another campaign
            continue
        if effective_min_score and (item.score or 0) < effective_min_score:
            continue
        if campaign.sources and item.source not in campaign.sources:
            continue
        candidate_ids.append(item.id)

    count = attach_queue_items_to_campaign(campaign_id, candidate_ids)
    logger.info("Attached %d queue items to campaign %s", count, campaign_id)
    return candidate_ids


# ---------------------------------------------------------------------------
# Sent helper
# ---------------------------------------------------------------------------


def mark_campaign_sent(
    campaign_id: str,
    application_id: str,
    offer_id: str,
    queue_item_id: str = "",
) -> CampaignActivity:
    """Record a SENT activity and check if the campaign target is reached."""
    activity = record_campaign_activity(
        campaign_id,
        CampaignActivityType.SENT,
        offer_id=offer_id,
        application_id=application_id,
        queue_item_id=queue_item_id,
    )
    complete_campaign_if_target_reached(campaign_id)
    return activity


# ---------------------------------------------------------------------------
# Auto-complete
# ---------------------------------------------------------------------------


def complete_campaign_if_target_reached(campaign_id: str) -> bool:
    """Mark campaign COMPLETED if sent_count >= target_count.

    Returns ``True`` if the campaign was just completed.
    """
    campaign = get_campaign_by_id(campaign_id)
    if campaign is None:
        return False
    if campaign.status != CampaignStatus.ACTIVE:
        return False
    if campaign.sent_count >= campaign.target_count:
        update_campaign_status(campaign_id, CampaignStatus.COMPLETED)
        logger.info(
            "Campaign %s completed (%d/%d sent)",
            campaign_id,
            campaign.sent_count,
            campaign.target_count,
        )
        return True
    return False


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def generate_campaign_summary(campaign_id: str) -> str:
    """Return a human-readable campaign summary string."""
    progress = get_campaign_progress(campaign_id)
    campaign = get_campaign_by_id(campaign_id)
    if progress is None or campaign is None:
        return "Campaign not found."

    lines: list[str] = [
        f"**{progress.campaign_name}** — {campaign.status}",
        f"Target: {progress.target} | Sent: {progress.sent} | Remaining: {progress.remaining}",
        f"Progress: {progress.progress_pct:.0f}%",
    ]

    if progress.filled_not_sent:
        lines.append(f"Filled but not yet sent: {progress.filled_not_sent}")
    if progress.skipped:
        lines.append(f"Skipped: {progress.skipped}")
    if progress.failed:
        lines.append(f"Failed: {progress.failed}")
    if progress.follow_ups:
        lines.append(f"Follow-ups recorded: {progress.follow_ups}")

    if progress.remaining == 0:
        lines.append("Target reached! You can mark this campaign complete.")
    elif progress.queue_shortage:
        lines.append(f"⚠️ {progress.queue_shortage_message}")
    else:
        lines.append(
            f"Queue has {progress.queued_available} offer(s) available. "
            f"You need {progress.remaining} more sent to hit your target."
        )

    return "\n".join(lines)
