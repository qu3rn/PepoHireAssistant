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
    FAILED = "failed"
    REPLY_RECEIVED = "reply_received"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    OFFER = "offer"


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
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    events: list[ApplicationEvent] = Field(default_factory=list)
