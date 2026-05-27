"""Pydantic models for offers and applications."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ApplicationStatus(StrEnum):
    """Lifecycle statuses for a job application."""

    NEW = "new"
    MATCHED = "matched"
    SKIPPED = "skipped"
    READY_TO_SEND = "ready_to_send"
    SENT = "sent"
    FOLLOW_UP_DUE = "follow_up_due"
    FOLLOW_UP_SENT = "follow_up_sent"
    REPLY_RECEIVED = "reply_received"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    OFFER = "offer"
    NO_RESPONSE = "no_response"
    ARCHIVED = "archived"
    FAILED = "failed"


class Decision(StrEnum):
    """LLM / scoring decision for an offer."""

    APPLY = "apply"
    SKIP = "skip"
    MAYBE = "maybe"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Offer(BaseModel):
    """A job offer scraped or added manually."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str = ""
    url: str
    title: str
    company: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    currency: str = "PLN"
    contract: str = ""
    location: str = ""
    description: str = ""
    technologies: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    score: int | None = None
    decision: Decision | None = None
    decision_reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    # Extraction metadata (populated when imported via URL)
    extraction_source: str = ""
    extraction_confidence: float = 0.0
    extraction_warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Batch URL import
# ---------------------------------------------------------------------------


class ImportStatus(StrEnum):
    """Result status for a single URL in a batch import."""

    IMPORTED = "imported"
    DUPLICATE = "duplicate"
    FAILED = "failed"
    INVALID = "invalid"
    SKIPPED_LIMIT = "skipped_limit"


class BatchImportItemResult(BaseModel):
    """Result for one URL in a batch import."""

    url: str
    status: ImportStatus
    offer_id: str | None = None
    company: str = ""
    title: str = ""
    score: int | None = None
    decision: Decision | None = None
    error: str = ""


class BatchImportResult(BaseModel):
    """Aggregated result of a batch URL import."""

    items: list[BatchImportItemResult] = Field(default_factory=list)

    @property
    def imported_count(self) -> int:
        return sum(1 for i in self.items if i.status == ImportStatus.IMPORTED)

    @property
    def duplicate_count(self) -> int:
        return sum(1 for i in self.items if i.status == ImportStatus.DUPLICATE)

    @property
    def failed_count(self) -> int:
        return sum(1 for i in self.items if i.status == ImportStatus.FAILED)

    @property
    def invalid_count(self) -> int:
        return sum(1 for i in self.items if i.status == ImportStatus.INVALID)

    @property
    def skipped_limit_count(self) -> int:
        return sum(1 for i in self.items if i.status == ImportStatus.SKIPPED_LIMIT)

    @property
    def scored_count(self) -> int:
        return sum(
            1
            for i in self.items
            if i.status == ImportStatus.IMPORTED and i.score is not None
        )


class FillStatus(StrEnum):
    """Status of a Playwright form-filling attempt."""

    FILLED = "filled"
    PARTIAL = "partial"
    FAILED = "failed"


class FillResult(BaseModel):
    """Structured result returned by portal fillers."""

    status: FillStatus
    source: str = ""
    offer_id: str = ""
    url: str = ""
    fields_filled: list[str] = Field(default_factory=list)
    fields_missing: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    # Debug artifact links (populated when form_filling.debug is enabled)
    debug_run_id: str = ""
    screenshot_path: str = ""
    form_snapshot_path: str = ""
    step_log_path: str = ""
    # Generated answers summary (populated when answer generation is enabled)
    generated_answers: list[dict] = Field(default_factory=list)


class ApplicationEvent(BaseModel):
    """A timestamped event in the application lifecycle."""

    timestamp: datetime = Field(default_factory=_utcnow)
    event: str
    details: str = ""


class Application(BaseModel):
    """A job application linked to an offer."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    offer_id: str
    source: str = ""
    url: str = ""
    company: str = ""
    title: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    currency: str = "PLN"
    contract: str = ""
    location: str = ""
    status: ApplicationStatus = ApplicationStatus.NEW
    score: int | None = None
    cv_file: str = ""
    cover_letter_file: str = ""
    notes: str = ""
    # CV profile tracking (populated when multi-CV selection is used)
    selected_cv_id: str = ""
    selected_cv_name: str = ""
    selected_cv_path: str = ""
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    events: list[ApplicationEvent] = Field(default_factory=list)
    # Follow-up tracking fields (optional; None when not yet set)
    sent_at: datetime | None = None
    follow_up_due_at: datetime | None = None
    follow_up_sent_at: datetime | None = None
    last_contact_at: datetime | None = None
    next_action_at: datetime | None = None
    next_action_type: str = ""        # "follow_up" | "interview" | ""
    next_action_note: str = ""
    interview_at: datetime | None = None
    interview_id: str = ""           # FK to Interview.id
    calendar_event_id: str = ""      # Google Calendar event id (if created)
    company_contact_name: str = ""
    company_contact_email: str = ""
    outcome: str = ""                 # free-text outcome note
    reminder_snoozed_until: datetime | None = None


# ---------------------------------------------------------------------------
# Gmail email matching
# ---------------------------------------------------------------------------


class EmailClassification(StrEnum):
    """Classification of an email relative to a job application."""

    REPLY_RECEIVED = "reply_received"
    INTERVIEW_INVITATION = "interview_invitation"
    REJECTION = "rejection"
    OFFER = "offer"
    RECRUITER_SCREENING = "recruiter_screening"
    AUTOMATED_CONFIRMATION = "automated_confirmation"
    UNRELATED = "unrelated"
    UNKNOWN = "unknown"


class EmailMatchStatus(StrEnum):
    """Whether the user has acted on an email match."""

    PENDING = "pending"
    APPLIED = "applied"
    IGNORED = "ignored"


class EmailMatch(BaseModel):
    """A Gmail message matched to a job application."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    application_id: str
    email_message_id: str          # Gmail message id (unique)
    thread_id: str = ""
    from_email: str = ""
    from_name: str = ""
    subject: str = ""
    snippet: str = ""
    received_at: datetime
    matched_company: str = ""
    matched_application_title: str = ""
    match_score: int = 0
    classification: EmailClassification = EmailClassification.UNKNOWN
    confidence: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    status_suggestion: str = "no_change"   # one of: reply_received|interview|rejected|offer|no_change
    status: EmailMatchStatus = EmailMatchStatus.PENDING
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Interview scheduling
# ---------------------------------------------------------------------------


class InterviewType(StrEnum):
    PHONE = "phone"
    VIDEO = "video"
    ONSITE = "onsite"
    TECHNICAL = "technical"
    HR = "hr"
    UNKNOWN = "unknown"


class InterviewStatus(StrEnum):
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    RESCHEDULED = "rescheduled"


class Interview(BaseModel):
    """A scheduled interview linked to a job application."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    application_id: str
    company: str = ""
    title: str = ""
    interview_at: datetime
    duration_minutes: int = 60
    timezone: str = "Europe/Warsaw"
    location: str = ""
    meeting_url: str = ""
    interview_type: InterviewType = InterviewType.UNKNOWN
    participants: list[str] = Field(default_factory=list)
    notes: str = ""
    source: str = "manual"          # "manual" | "gmail" | "calendar"
    gmail_match_id: str = ""
    calendar_event_id: str = ""
    status: InterviewStatus = InterviewStatus.SCHEDULED
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Apply queue
# ---------------------------------------------------------------------------


class ApplyQueueItemStatus(StrEnum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    FILLED = "filled"
    SENT = "sent"
    SKIPPED = "skipped"
    FAILED = "failed"


class ApplyQueueItem(BaseModel):
    """An entry in the rapid-apply queue."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    offer_id: str
    company: str = ""
    title: str = ""
    source: str = ""
    url: str = ""
    score: int | None = None
    priority_score: float = 0.0
    selected_cv_id: str = ""
    selected_cv_name: str = ""
    status: ApplyQueueItemStatus = ApplyQueueItemStatus.QUEUED
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
