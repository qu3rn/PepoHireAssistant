"""Google Calendar integration for interview scheduling.

Scope: https://www.googleapis.com/auth/calendar.events

Calendar events are only created after explicit user confirmation.
OAuth tokens and credentials are never logged.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cv_sender.config import CalendarConfig
from cv_sender.models import Interview

logger = logging.getLogger(__name__)

_CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def is_calendar_configured(cfg: CalendarConfig | None = None) -> bool:
    """Return True if Calendar credentials exist and the feature is enabled."""
    if cfg is None:
        from cv_sender.config import load_settings  # noqa: PLC0415
        cfg = load_settings().calendar
    if not cfg.enabled:
        return False
    return Path(cfg.credentials_path).exists()


def is_calendar_authenticated(cfg: CalendarConfig | None = None) -> bool:
    """Return True if a valid calendar token file already exists."""
    if cfg is None:
        from cv_sender.config import load_settings  # noqa: PLC0415
        cfg = load_settings().calendar
    return Path(cfg.token_path).exists()


# ---------------------------------------------------------------------------
# Service initialisation
# ---------------------------------------------------------------------------


def get_calendar_service(cfg: CalendarConfig | None = None) -> Any:
    """Build and return an authenticated Google Calendar API service object.

    Raises ``ImportError`` if the Google API client library is not installed.
    Raises ``FileNotFoundError`` if the credentials file is missing.
    Raises ``RuntimeError`` for OAuth / API errors.
    """
    try:
        from google.auth.transport.requests import Request  # noqa: PLC0415
        from google.oauth2.credentials import Credentials  # noqa: PLC0415
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: PLC0415
        from googleapiclient.discovery import build  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "Google API client libraries not installed. "
            "Run: pip install google-api-python-client google-auth google-auth-oauthlib google-auth-httplib2"
        ) from exc

    if cfg is None:
        from cv_sender.config import load_settings  # noqa: PLC0415
        cfg = load_settings().calendar

    creds_path = Path(cfg.credentials_path)
    token_path = Path(cfg.token_path)

    if not creds_path.exists():
        raise FileNotFoundError(
            f"Calendar credentials not found at '{cfg.credentials_path}'. "
            "Download OAuth 2.0 credentials from Google Cloud Console and place them there."
        )

    creds: Credentials | None = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), [_CALENDAR_EVENTS_SCOPE])

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:  # noqa: BLE001
                logger.warning("Calendar token refresh failed: %s – re-running OAuth flow.", exc)
                creds = None
        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(creds_path), [_CALENDAR_EVENTS_SCOPE]
            )
            creds = flow.run_local_server(port=0)

        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return build("calendar", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# Event payload builder (pure, no I/O)
# ---------------------------------------------------------------------------


def build_calendar_event_body(interview: Interview, cfg: CalendarConfig) -> dict:
    """Return the dict payload for a Google Calendar event.

    No API calls are made here; this function is kept pure for testability.
    """
    tz = cfg.timezone or "Europe/Warsaw"
    start_dt = interview.interview_at
    end_dt = start_dt + timedelta(minutes=interview.duration_minutes)

    # Format as RFC 3339 with timezone offset string (Calendar API requires this)
    def _fmt(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    description_parts = [
        f"Company: {interview.company}",
        f"Role: {interview.title}",
    ]
    if interview.meeting_url:
        description_parts.append(f"Meeting URL: {interview.meeting_url}")
    if interview.notes:
        description_parts.append(f"Notes: {interview.notes}")
    description_parts.append("Source: Job Assistant (cv-sender)")

    location = interview.meeting_url or interview.location or ""

    body: dict[str, Any] = {
        "summary": f"Interview: {interview.company} — {interview.title}",
        "description": "\n".join(description_parts),
        "location": location,
        "start": {"dateTime": _fmt(start_dt), "timeZone": tz},
        "end": {"dateTime": _fmt(end_dt), "timeZone": tz},
    }

    if cfg.add_reminders and cfg.reminder_minutes_before:
        body["reminders"] = {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": m}
                for m in cfg.reminder_minutes_before
            ],
        }
    else:
        body["reminders"] = {"useDefault": True}

    return body


# ---------------------------------------------------------------------------
# CRUD wrappers
# ---------------------------------------------------------------------------


def create_calendar_event_for_interview(
    interview: Interview, cfg: CalendarConfig | None = None
) -> str:
    """Create a Google Calendar event and return the event id.

    Raises on any API error.
    """
    if cfg is None:
        from cv_sender.config import load_settings  # noqa: PLC0415
        cfg = load_settings().calendar

    service = get_calendar_service(cfg)
    body = build_calendar_event_body(interview, cfg)

    event = (
        service.events()
        .insert(calendarId=cfg.calendar_id, body=body)
        .execute()
    )
    event_id = event.get("id", "")
    logger.info("Created calendar event %s for interview %s", event_id, interview.id)
    return event_id


def update_calendar_event_for_interview(
    interview: Interview, cfg: CalendarConfig | None = None
) -> None:
    """Update the existing Calendar event for *interview*.

    No-op if ``interview.calendar_event_id`` is empty.
    """
    if not interview.calendar_event_id:
        return
    if cfg is None:
        from cv_sender.config import load_settings  # noqa: PLC0415
        cfg = load_settings().calendar

    service = get_calendar_service(cfg)
    body = build_calendar_event_body(interview, cfg)

    service.events().update(
        calendarId=cfg.calendar_id,
        eventId=interview.calendar_event_id,
        body=body,
    ).execute()
    logger.info("Updated calendar event %s for interview %s", interview.calendar_event_id, interview.id)


def delete_calendar_event_for_interview(
    interview: Interview, cfg: CalendarConfig | None = None
) -> None:
    """Delete the Calendar event for *interview*.

    No-op if ``interview.calendar_event_id`` is empty.
    """
    if not interview.calendar_event_id:
        return
    if cfg is None:
        from cv_sender.config import load_settings  # noqa: PLC0415
        cfg = load_settings().calendar

    service = get_calendar_service(cfg)
    service.events().delete(
        calendarId=cfg.calendar_id,
        eventId=interview.calendar_event_id,
    ).execute()
    logger.info("Deleted calendar event %s for interview %s", interview.calendar_event_id, interview.id)
