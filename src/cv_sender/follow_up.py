"""Follow-up tracking logic: due date calculation, reminders, stale detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from cv_sender.config import FollowUpConfig

# Warsaw timezone offset (UTC+1 in winter, UTC+2 in summer).
# Using a fixed-offset zone avoids the pytz/zoneinfo dependency issue while
# keeping the "local" flavour the spec requests.
try:
    from zoneinfo import ZoneInfo

    _WARSAW = ZoneInfo("Europe/Warsaw")
except ImportError:  # Python < 3.9 fallback
    _WARSAW = timezone(timedelta(hours=1))  # type: ignore[assignment]


def _now_warsaw() -> datetime:
    return datetime.now(_WARSAW)


def _to_warsaw(dt: datetime) -> datetime:
    """Convert *dt* to Europe/Warsaw if it has no tzinfo, otherwise convert."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_WARSAW)
    return dt.astimezone(_WARSAW)


# ---------------------------------------------------------------------------
# Due date calculation
# ---------------------------------------------------------------------------


def calculate_follow_up_due(
    sent_at: datetime,
    cfg: FollowUpConfig,
) -> datetime:
    """Return the follow-up due datetime for an application sent at *sent_at*.

    Adds ``cfg.default_follow_up_after_days`` business days (or calendar days
    when ``cfg.allow_weekend_due_dates`` is True).  Saturdays and Sundays are
    skipped when weekend due dates are not allowed.

    The returned datetime is in UTC.
    """
    candidate = _to_warsaw(sent_at) + timedelta(days=cfg.default_follow_up_after_days)

    if not cfg.allow_weekend_due_dates:
        # weekday(): Monday=0 … Sunday=6
        while candidate.weekday() >= 5:  # Saturday or Sunday
            candidate += timedelta(days=1)

    # Store in UTC so the value is unambiguous in JSON
    return candidate.astimezone(UTC)


def is_follow_up_due(app: "Application", now: datetime | None = None) -> bool:  # noqa: F821
    """Return True if *app* has a follow-up that is due right now."""
    if app.follow_up_due_at is None:
        return False
    if app.reminder_snoozed_until is not None:
        _now = now or datetime.now(UTC)
        if app.reminder_snoozed_until > _now:
            return False
    _now = now or datetime.now(UTC)
    return app.follow_up_due_at <= _now


def is_stale(app: "Application", cfg: FollowUpConfig, now: datetime | None = None) -> bool:
    """Return True if *app* has had no contact for ``mark_no_response_after_days``."""
    if app.status not in ("sent", "follow_up_due", "follow_up_sent"):
        return False
    _now = now or datetime.now(UTC)
    last = app.last_contact_at or app.sent_at
    if last is None:
        return False
    return (_now - last).days >= cfg.mark_no_response_after_days


# ---------------------------------------------------------------------------
# Follow-up message generator
# ---------------------------------------------------------------------------


def generate_follow_up_message(
    app: "Application",
    candidate_name: str = "",
) -> str:
    """Generate a simple plain-text follow-up message for manual use.

    The message is returned as a string and is **never** sent automatically.
    """
    name = candidate_name.strip() or "I"
    title = app.title or "the position"
    company = app.company or "your company"

    sent_info = ""
    if app.sent_at:
        sent_info = f" on {app.sent_at.strftime('%B %d, %Y')}"

    greeting = "Hi,"
    body = (
        f"{greeting}\n\n"
        f"{name} wanted to follow up on the application for the "
        f"**{title}** role at **{company}**{sent_info}.\n\n"
        f"I am still very interested in the opportunity and would be happy to "
        f"provide any additional information or complete any further steps in "
        f"your process.\n\n"
        f"Thank you for your time."
    )
    return body
