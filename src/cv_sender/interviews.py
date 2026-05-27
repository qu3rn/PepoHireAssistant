"""Interview service functions.

All external calendar writes require explicit caller opt-in via ``create_calendar_event=True``.
No auto-scheduling or auto-updating of calendar events takes place.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from cv_sender.models import (
    Application,
    ApplicationEvent,
    ApplicationStatus,
    Interview,
    InterviewStatus,
    InterviewType,
)
from cv_sender.storage import (
    add_interview,
    get_application_by_id,
    get_interview_by_id,
    load_interviews,
    update_application,
    update_interview,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _append_event(app: Application, event: str, details: str = "") -> Application:
    new_events = list(app.events) + [ApplicationEvent(event=event, details=details)]
    return app.model_copy(update={"events": new_events})


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


def create_interview(
    application_id: str,
    interview_data: dict[str, Any],
    create_calendar_event: bool = False,
) -> tuple[bool, str, Interview | None]:
    """Create a new interview and link it to an application.

    Returns ``(ok, message, interview_or_None)``.
    Calendar event is only created when *create_calendar_event* is ``True``.
    """
    app = get_application_by_id(application_id)
    if app is None:
        return False, f"Application {application_id!r} not found.", None

    try:
        interview = Interview(application_id=application_id, **interview_data)
    except Exception as exc:  # noqa: BLE001
        return False, f"Invalid interview data: {exc}", None

    # Populate company/title from application if not provided
    if not interview.company and app.company:
        interview = interview.model_copy(update={"company": app.company})
    if not interview.title and app.title:
        interview = interview.model_copy(update={"title": app.title})

    add_interview(interview)

    # Update application
    updated_app = app.model_copy(
        update={
            "status": ApplicationStatus.INTERVIEW,
            "interview_at": interview.interview_at,
            "interview_id": interview.id,
        }
    )
    updated_app = _append_event(
        updated_app,
        "interview_scheduled",
        f"Scheduled for {interview.interview_at.isoformat()} via {interview.source}",
    )

    # Optionally create calendar event
    if create_calendar_event:
        try:
            from cv_sender.calendar_integration import (  # noqa: PLC0415
                create_calendar_event_for_interview,
            )
            from cv_sender.config import load_settings  # noqa: PLC0415

            cfg = load_settings().calendar
            event_id = create_calendar_event_for_interview(interview, cfg)
            interview = interview.model_copy(update={"calendar_event_id": event_id})
            update_interview(interview)
            updated_app = updated_app.model_copy(update={"calendar_event_id": event_id})
            updated_app = _append_event(
                updated_app,
                "calendar_event_created",
                f"Google Calendar event {event_id} created",
            )
        except Exception as exc:  # noqa: BLE001
            # Calendar failure must not roll back the interview itself
            updated_app = _append_event(
                updated_app,
                "calendar_event_failed",
                str(exc),
            )

    update_application(updated_app)
    return True, "Interview scheduled.", interview


def schedule_interview_from_email_match(
    match_id: str,
    interview_data: dict[str, Any],
    create_calendar_event: bool = False,
) -> tuple[bool, str, Interview | None]:
    """Create an interview originating from a Gmail email match.

    The *interview_data* dict is merged with ``source="gmail"`` and
    ``gmail_match_id=match_id`` before calling :func:`create_interview`.
    """
    from cv_sender.storage import get_email_match_by_id  # noqa: PLC0415

    match = get_email_match_by_id(match_id)
    if match is None:
        return False, f"Email match {match_id!r} not found.", None

    merged = {
        "source": "gmail",
        "gmail_match_id": match_id,
        **interview_data,
    }
    return create_interview(match.application_id, merged, create_calendar_event)


def list_upcoming_interviews(now: datetime | None = None) -> list[Interview]:
    """Return interviews that are scheduled and in the future."""
    now = now or _utcnow()
    return [
        i
        for i in load_interviews()
        if i.status == InterviewStatus.SCHEDULED and i.interview_at >= now
    ]


def list_past_interviews(now: datetime | None = None) -> list[Interview]:
    """Return interviews that are in the past or completed/cancelled."""
    now = now or _utcnow()
    return [
        i
        for i in load_interviews()
        if i.interview_at < now or i.status in (InterviewStatus.COMPLETED, InterviewStatus.CANCELLED)
    ]


def mark_interview_completed(interview_id: str) -> tuple[bool, str]:
    """Mark an interview as completed."""
    interview = get_interview_by_id(interview_id)
    if interview is None:
        return False, f"Interview {interview_id!r} not found."
    updated = interview.model_copy(
        update={"status": InterviewStatus.COMPLETED, "updated_at": _utcnow()}
    )
    update_interview(updated)
    return True, "Interview marked as completed."


def cancel_interview(interview_id: str) -> tuple[bool, str]:
    """Cancel an interview and optionally delete the calendar event."""
    interview = get_interview_by_id(interview_id)
    if interview is None:
        return False, f"Interview {interview_id!r} not found."
    updated = interview.model_copy(
        update={"status": InterviewStatus.CANCELLED, "updated_at": _utcnow()}
    )
    update_interview(updated)
    return True, "Interview cancelled."


def reschedule_interview(
    interview_id: str,
    new_datetime: datetime,
    update_calendar: bool = False,
) -> tuple[bool, str]:
    """Update the scheduled time for an interview.

    Calendar event is only updated when *update_calendar* is ``True``.
    """
    interview = get_interview_by_id(interview_id)
    if interview is None:
        return False, f"Interview {interview_id!r} not found."

    updated = interview.model_copy(
        update={
            "interview_at": new_datetime,
            "status": InterviewStatus.RESCHEDULED,
            "updated_at": _utcnow(),
        }
    )
    update_interview(updated)

    # Propagate new time to the application
    app = get_application_by_id(interview.application_id)
    if app is not None:
        updated_app = app.model_copy(update={"interview_at": new_datetime})
        updated_app = _append_event(
            updated_app,
            "interview_rescheduled",
            f"Rescheduled to {new_datetime.isoformat()}",
        )
        update_application(updated_app)

    if update_calendar and updated.calendar_event_id:
        try:
            from cv_sender.calendar_integration import (  # noqa: PLC0415
                update_calendar_event_for_interview,
            )
            from cv_sender.config import load_settings  # noqa: PLC0415

            update_calendar_event_for_interview(updated, load_settings().calendar)
        except Exception as exc:  # noqa: BLE001
            return True, f"Interview rescheduled, but calendar update failed: {exc}"

    return True, "Interview rescheduled."


def get_interview_for_application(app_id: str) -> Interview | None:
    """Return the most-recently created interview linked to *app_id*, or ``None``."""
    matches = [i for i in load_interviews() if i.application_id == app_id]
    if not matches:
        return None
    return sorted(matches, key=lambda i: i.created_at, reverse=True)[0]
