"""JSON-file-based storage for offers and applications."""

from __future__ import annotations

import json
import os
from pathlib import Path

from cv_sender.models import Application, ApplyQueueItem, Campaign, CampaignActivity, EmailMatch, Interview, Offer

_DEFAULT_OFFERS = Path(os.getenv("OFFERS_PATH", "data/offers.json"))
_DEFAULT_APPLICATIONS = Path(os.getenv("APPLICATIONS_PATH", "data/applications.json"))
_DEFAULT_EMAIL_MATCHES = Path(os.getenv("EMAIL_MATCHES_PATH", "data/email_matches.json"))
_DEFAULT_INTERVIEWS = Path(os.getenv("INTERVIEWS_PATH", "data/interviews.json"))
_DEFAULT_APPLY_QUEUE = Path(os.getenv("APPLY_QUEUE_PATH", "data/apply_queue.json"))
_DEFAULT_CAMPAIGNS = Path(os.getenv("CAMPAIGNS_PATH", "data/campaigns.json"))
_DEFAULT_CAMPAIGN_ACTIVITIES = Path(os.getenv("CAMPAIGN_ACTIVITIES_PATH", "data/campaign_activities.json"))


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> list:
    """Read a JSON array from *path*, creating an empty file if necessary."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        return []
    with path.open(encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError:
            return []


def _write_json(path: Path, data: list) -> None:
    """Write a JSON array to *path* with pretty formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------


def load_offers(path: Path | None = None) -> list[Offer]:
    """Load all offers from storage."""
    raw = _read_json(path or _DEFAULT_OFFERS)
    return [Offer.model_validate(item) for item in raw]


def save_offers(offers: list[Offer], path: Path | None = None) -> None:
    """Persist all offers to storage."""
    _write_json(
        path or _DEFAULT_OFFERS,
        [o.model_dump(mode="json") for o in offers],
    )


def add_offer(offer: Offer, path: Path | None = None) -> bool:
    """Add *offer* to storage.

    Returns ``False`` (without saving) if an offer with the same URL already
    exists – this prevents duplicates.
    """
    offers = load_offers(path)
    if any(o.url == offer.url for o in offers):
        return False
    offers.append(offer)
    save_offers(offers, path)
    return True


def get_offer_by_id(offer_id: str, path: Path | None = None) -> Offer | None:
    """Return the offer with *offer_id*, or ``None`` if not found."""
    return next((o for o in load_offers(path) if o.id == offer_id), None)


def update_offer(offer: Offer, path: Path | None = None) -> None:
    """Replace the stored offer that has the same id as *offer*."""
    offers = load_offers(path)
    updated = [offer if o.id == offer.id else o for o in offers]
    save_offers(updated, path)


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


def load_applications(path: Path | None = None) -> list[Application]:
    """Load all applications from storage."""
    raw = _read_json(path or _DEFAULT_APPLICATIONS)
    return [Application.model_validate(item) for item in raw]


def save_applications(applications: list[Application], path: Path | None = None) -> None:
    """Persist all applications to storage."""
    _write_json(
        path or _DEFAULT_APPLICATIONS,
        [a.model_dump(mode="json") for a in applications],
    )


def add_application(application: Application, path: Path | None = None) -> None:
    """Append *application* to storage (duplicates allowed – each run is unique)."""
    apps = load_applications(path)
    apps.append(application)
    save_applications(apps, path)


def get_application_by_id(app_id: str, path: Path | None = None) -> Application | None:
    """Return the application with *app_id*, or ``None`` if not found."""
    return next((a for a in load_applications(path) if a.id == app_id), None)


def update_application(application: Application, path: Path | None = None) -> None:
    """Replace the stored application that has the same id as *application*."""
    apps = load_applications(path)
    updated = [application if a.id == application.id else a for a in apps]
    save_applications(updated, path)


# ---------------------------------------------------------------------------
# Email matches
# ---------------------------------------------------------------------------


def load_email_matches(path: Path | None = None) -> list[EmailMatch]:
    """Load all email matches from storage."""
    raw = _read_json(path or _DEFAULT_EMAIL_MATCHES)
    return [EmailMatch.model_validate(item) for item in raw]


def save_email_matches(matches: list[EmailMatch], path: Path | None = None) -> None:
    """Persist all email matches to storage."""
    _write_json(
        path or _DEFAULT_EMAIL_MATCHES,
        [m.model_dump(mode="json") for m in matches],
    )


def add_email_match(match: EmailMatch, path: Path | None = None) -> bool:
    """Add *match* to storage.

    Returns ``False`` without saving if a match with the same Gmail message id
    already exists (prevents duplicates).
    """
    matches = load_email_matches(path)
    if any(m.email_message_id == match.email_message_id for m in matches):
        return False
    matches.append(match)
    save_email_matches(matches, path)
    return True


def get_email_match_by_id(match_id: str, path: Path | None = None) -> EmailMatch | None:
    """Return the email match with *match_id*, or ``None``."""
    return next((m for m in load_email_matches(path) if m.id == match_id), None)


def update_email_match(match: EmailMatch, path: Path | None = None) -> None:
    """Replace the stored email match that has the same id as *match*."""
    matches = load_email_matches(path)
    updated = [match if m.id == match.id else m for m in matches]
    save_email_matches(updated, path)


# ---------------------------------------------------------------------------
# Interviews
# ---------------------------------------------------------------------------


def load_interviews(path: Path | None = None) -> list[Interview]:
    """Load all interviews from storage."""
    raw = _read_json(path or _DEFAULT_INTERVIEWS)
    return [Interview.model_validate(item) for item in raw]


def save_interviews(interviews: list[Interview], path: Path | None = None) -> None:
    """Persist all interviews to storage."""
    _write_json(
        path or _DEFAULT_INTERVIEWS,
        [i.model_dump(mode="json") for i in interviews],
    )


def add_interview(interview: Interview, path: Path | None = None) -> None:
    """Append *interview* to storage."""
    interviews = load_interviews(path)
    interviews.append(interview)
    save_interviews(interviews, path)


def get_interview_by_id(interview_id: str, path: Path | None = None) -> Interview | None:
    """Return the interview with *interview_id*, or ``None``."""
    return next((i for i in load_interviews(path) if i.id == interview_id), None)


def update_interview(interview: Interview, path: Path | None = None) -> None:
    """Replace the stored interview that has the same id as *interview*."""
    interviews = load_interviews(path)
    updated = [interview if i.id == interview.id else i for i in interviews]
    save_interviews(updated, path)


# ---------------------------------------------------------------------------
# Apply queue
# ---------------------------------------------------------------------------


def load_apply_queue(path: Path | None = None) -> list[ApplyQueueItem]:
    """Load the apply queue from storage."""
    raw = _read_json(path or _DEFAULT_APPLY_QUEUE)
    return [ApplyQueueItem.model_validate(item) for item in raw]


def save_apply_queue(queue: list[ApplyQueueItem], path: Path | None = None) -> None:
    """Persist the apply queue to storage."""
    _write_json(
        path or _DEFAULT_APPLY_QUEUE,
        [item.model_dump(mode="json") for item in queue],
    )


def add_to_apply_queue(item: ApplyQueueItem, path: Path | None = None) -> bool:
    """Add *item* to the queue.

    Returns ``False`` without saving if an item for the same offer_id already
    exists in a non-terminal state.
    """
    from cv_sender.models import ApplyQueueItemStatus  # noqa: PLC0415

    queue = load_apply_queue(path)
    terminal = {ApplyQueueItemStatus.SENT, ApplyQueueItemStatus.SKIPPED, ApplyQueueItemStatus.FAILED}
    if any(q.offer_id == item.offer_id and q.status not in terminal for q in queue):
        return False
    queue.append(item)
    save_apply_queue(queue, path)
    return True


def get_queue_item_by_id(item_id: str, path: Path | None = None) -> ApplyQueueItem | None:
    """Return the queue item with *item_id*, or ``None``."""
    return next((q for q in load_apply_queue(path) if q.id == item_id), None)


def update_queue_item(item: ApplyQueueItem, path: Path | None = None) -> None:
    """Replace the stored queue item that has the same id as *item*."""
    queue = load_apply_queue(path)
    updated = [item if q.id == item.id else q for q in queue]
    save_apply_queue(updated, path)


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------


def load_campaigns(path: Path | None = None) -> list[Campaign]:
    """Load all campaigns from storage."""
    raw = _read_json(path or _DEFAULT_CAMPAIGNS)
    return [Campaign.model_validate(item) for item in raw]


def save_campaigns(campaigns: list[Campaign], path: Path | None = None) -> None:
    """Persist all campaigns to storage."""
    _write_json(
        path or _DEFAULT_CAMPAIGNS,
        [c.model_dump(mode="json") for c in campaigns],
    )


def add_campaign(campaign: Campaign, path: Path | None = None) -> None:
    """Append *campaign* to storage."""
    campaigns = load_campaigns(path)
    campaigns.append(campaign)
    save_campaigns(campaigns, path)


def get_campaign_by_id(campaign_id: str, path: Path | None = None) -> Campaign | None:
    """Return the campaign with *campaign_id*, or ``None``."""
    return next((c for c in load_campaigns(path) if c.id == campaign_id), None)


def update_campaign(campaign: Campaign, path: Path | None = None) -> None:
    """Replace the stored campaign that has the same id as *campaign*."""
    campaigns = load_campaigns(path)
    updated = [campaign if c.id == campaign.id else c for c in campaigns]
    save_campaigns(updated, path)


# ---------------------------------------------------------------------------
# Campaign activities
# ---------------------------------------------------------------------------


def load_campaign_activities(path: Path | None = None) -> list[CampaignActivity]:
    """Load all campaign activities from storage."""
    raw = _read_json(path or _DEFAULT_CAMPAIGN_ACTIVITIES)
    return [CampaignActivity.model_validate(item) for item in raw]


def save_campaign_activities(
    activities: list[CampaignActivity],
    path: Path | None = None,
) -> None:
    """Persist all campaign activities to storage."""
    _write_json(
        path or _DEFAULT_CAMPAIGN_ACTIVITIES,
        [a.model_dump(mode="json") for a in activities],
    )


def add_campaign_activity(
    activity: CampaignActivity,
    path: Path | None = None,
) -> None:
    """Append *activity* to storage."""
    activities = load_campaign_activities(path)
    activities.append(activity)
    save_campaign_activities(activities, path)


def get_campaign_activities(
    campaign_id: str,
    path: Path | None = None,
) -> list[CampaignActivity]:
    """Return all activities for *campaign_id*, sorted by timestamp ascending."""
    all_activities = load_campaign_activities(path)
    filtered = [a for a in all_activities if a.campaign_id == campaign_id]
    return sorted(filtered, key=lambda a: a.timestamp)
