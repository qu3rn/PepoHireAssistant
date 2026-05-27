"""Base types and utilities shared by all job collectors."""

from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Search criteria
# ---------------------------------------------------------------------------


@dataclass
class JobSearchCriteria:
    """Parameters that drive a job-offer collection run."""

    keywords: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    seniority: list[str] = field(default_factory=list)
    contract_types: list[str] = field(default_factory=list)
    min_salary_b2b: int = 0
    require_salary: bool = False
    max_offers_per_source: int = 30
    max_total_offers: int = 100
    exclude_keywords: list[str] = field(default_factory=list)
    request_delay_seconds: float = 1.5

    @classmethod
    def from_config(cls, cfg: Any) -> "JobSearchCriteria":
        """Build from a :class:`~cv_sender.config.JobSearchConfig` instance."""
        return cls(
            keywords=list(cfg.keywords),
            technologies=list(cfg.technologies),
            locations=list(cfg.locations),
            seniority=list(cfg.seniority),
            contract_types=list(cfg.contract_types),
            min_salary_b2b=cfg.min_salary_b2b,
            require_salary=cfg.require_salary,
            max_offers_per_source=cfg.max_offers_per_source,
            max_total_offers=cfg.max_total_offers,
            exclude_keywords=list(cfg.exclude_keywords),
            request_delay_seconds=cfg.request_delay_seconds,
        )

    @classmethod
    def emergency_react(cls) -> "JobSearchCriteria":
        """Preset for quickly collecting React / Frontend offers."""
        return cls(
            keywords=["React Developer", "Frontend Developer", "Frontend Engineer"],
            technologies=["React", "TypeScript", "Next.js"],
            locations=["Remote", "Poland"],
            seniority=["Mid", "Senior"],
            contract_types=["B2B", "UoP"],
            min_salary_b2b=0,
            require_salary=False,
            max_offers_per_source=30,
            max_total_offers=100,
            exclude_keywords=[],
            request_delay_seconds=1.5,
        )


# ---------------------------------------------------------------------------
# Collected offer
# ---------------------------------------------------------------------------


@dataclass
class CollectedOffer:
    """A raw offer gathered by a collector, before deduplication/import."""

    source: str
    url: str
    title: str
    company: str = ""
    location: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    currency: str = "PLN"
    contract: str = ""
    technologies: list[str] = field(default_factory=list)
    description_preview: str = ""
    collected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    raw_data: dict[str, Any] = field(default_factory=dict)

    # Set during filtering
    skip_reason: str = ""      # non-empty → filtered out
    is_duplicate: bool = False


# ---------------------------------------------------------------------------
# Collection result
# ---------------------------------------------------------------------------


@dataclass
class JobCollectionResult:
    """Aggregated result of a single-source collection run."""

    source: str
    raw_found_count: int = 0      # items returned by collector.search() before filtering
    collected_count: int = 0      # items that passed criteria filter (≤ raw_found_count)
    imported_count: int = 0
    duplicate_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    errors: list[str] = field(default_factory=list)
    offers: list[CollectedOffer] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    return text.lower().strip()


def _text_matches_any(text: str, terms: list[str]) -> bool:
    t = _normalize(text)
    return any(_normalize(term) in t for term in terms)


def passes_criteria_filter(offer: CollectedOffer, criteria: JobSearchCriteria) -> str:
    """Return '' if offer passes, or a non-empty reason string if it should be skipped."""
    combined = f"{offer.title} {offer.description_preview} {' '.join(offer.technologies)}"

    # Exclude keywords
    for kw in criteria.exclude_keywords:
        if _normalize(kw) in _normalize(combined):
            return f"excluded keyword: {kw!r}"

    # Must match at least one role keyword OR at least one technology
    keyword_match = _text_matches_any(offer.title + " " + offer.description_preview, criteria.keywords)
    tech_match = criteria.technologies and any(
        _normalize(t) in _normalize(combined) for t in criteria.technologies
    )
    if not keyword_match and not tech_match:
        return "no keyword or technology match"

    # Salary filter
    if criteria.require_salary and offer.salary_min is None and offer.salary_max is None:
        return "salary not visible (require_salary=true)"

    if criteria.min_salary_b2b > 0 and offer.salary_min is not None:
        if offer.salary_min < criteria.min_salary_b2b:
            return f"salary {offer.salary_min:.0f} below minimum {criteria.min_salary_b2b}"

    return ""


# ---------------------------------------------------------------------------
# Simple rate limiter (sleep between HTTP requests)
# ---------------------------------------------------------------------------


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


# ---------------------------------------------------------------------------
# Base collector
# ---------------------------------------------------------------------------


class BaseJobCollector(ABC):
    """Abstract base for source-specific job collectors."""

    source: str = ""

    @abstractmethod
    def search(self, criteria: JobSearchCriteria) -> list[CollectedOffer]:
        """Search the job board and return a list of :class:`CollectedOffer` objects.

        Must not raise — catch all errors internally and return partial results.
        """

    def collect_and_filter(self, criteria: JobSearchCriteria) -> JobCollectionResult:
        """Run :meth:`search`, apply filters, and return a :class:`JobCollectionResult`."""
        result = JobCollectionResult(source=self.source)
        try:
            raw = self.search(criteria)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"Collection failed: {exc}")
            logger.error("Collector %s failed: %s", self.source, exc)
            return result

        result.raw_found_count = len(raw)
        result.collected_count = len(raw)  # will be decremented by skipped below

        filtered_count = 0
        for offer in raw:
            skip = passes_criteria_filter(offer, criteria)
            if skip:
                offer.skip_reason = skip
                result.skipped_count += 1
                filtered_count += 1
            result.offers.append(offer)
            if not skip:
                result.imported_count += 1  # will be confirmed by import step

        # collected_count = raw minus filtered-out
        result.collected_count = result.raw_found_count - filtered_count

        return result


# ---------------------------------------------------------------------------
# HTTP fetch helper (used by collectors that don't need Playwright)
# ---------------------------------------------------------------------------


def _fetch_html(url: str, timeout: int = 15, headers: dict | None = None) -> str | None:
    """Fetch *url* with a simple requests GET; return HTML string or ``None``."""
    try:
        import requests  # noqa: PLC0415

        default_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9,pl;q=0.8",
        }
        if headers:
            default_headers.update(headers)
        resp = requests.get(url, headers=default_headers, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:  # noqa: BLE001
        logger.warning("HTTP fetch failed for %s: %s", url, exc)
        return None


def _fetch_json(url: str, timeout: int = 15, headers: dict | None = None) -> Any:
    """Fetch *url* and parse JSON; return parsed object or ``None``."""
    try:
        import requests  # noqa: PLC0415

        default_headers = {
            "User-Agent": "Mozilla/5.0 cv-sender/1.0 (personal job assistant)",
            "Accept": "application/json",
        }
        if headers:
            default_headers.update(headers)
        resp = requests.get(url, headers=default_headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("JSON fetch failed for %s: %s", url, exc)
        return None
