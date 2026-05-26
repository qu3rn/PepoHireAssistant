"""Shared types and helper functions used by all extractors."""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from html.parser import HTMLParser
from typing import Any

logger = logging.getLogger("cv_sender.extractors")

# ---------------------------------------------------------------------------
# OfferDraft – raw extraction result (not persisted directly)
# ---------------------------------------------------------------------------

ExtractionSource = str  # "json_ld" | "embedded_state" | "dom" | "generic" | "url_only"

JSON_LD = "json_ld"
EMBEDDED_STATE = "embedded_state"
DOM = "dom"
GENERIC = "generic"
URL_ONLY = "url_only"


class OfferDraft:
    """Mutable container for extracted offer fields.

    All fields default to empty/None so callers only set what they found.
    Confidence in [0.0, 1.0] — higher means more fields extracted reliably.
    """

    __slots__ = (
        "title", "company", "location", "contract", "salary_min", "salary_max",
        "currency", "description", "technologies",
        "extraction_source", "extraction_confidence", "extraction_warnings",
    )

    def __init__(self) -> None:
        self.title: str = ""
        self.company: str = ""
        self.location: str = ""
        self.contract: str = ""
        self.salary_min: float | None = None
        self.salary_max: float | None = None
        self.currency: str = "PLN"
        self.description: str = ""
        self.technologies: list[str] = []
        self.extraction_source: str = URL_ONLY
        self.extraction_confidence: float = 0.0
        self.extraction_warnings: list[str] = []

    def _filled_count(self) -> int:
        """Count of non-empty primary fields."""
        return sum([
            bool(self.title),
            bool(self.company),
            bool(self.location),
            self.salary_min is not None,
            bool(self.technologies),
        ])


# ---------------------------------------------------------------------------
# Base extractor
# ---------------------------------------------------------------------------


class BaseExtractor(ABC):
    """Abstract base for all source-specific extractors."""

    source: str = "unknown"

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        ...

    @abstractmethod
    def extract(self, url: str, html: str) -> OfferDraft:
        ...


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------


class _TextStripper(HTMLParser):
    """Minimal HTMLParser subclass that strips tags and collects text."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:  # noqa: D401
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def clean_description(text: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    if not text:
        return ""
    if "<" in text:
        parser = _TextStripper()
        try:
            parser.feed(text)
            text = parser.get_text()
        except Exception:  # noqa: BLE001
            text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:4000]


# ---------------------------------------------------------------------------
# JSON-LD and embedded state extraction
# ---------------------------------------------------------------------------


def parse_json_ld_jobposting(html: str) -> dict[str, Any] | None:
    """Find and return the first JSON-LD ``JobPosting`` object in *html*."""
    pattern = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.DOTALL | re.IGNORECASE,
    )
    for match in pattern.finditer(html):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        # data may be a list of schema objects
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            item_type = item.get("@type", "")
            types = item_type if isinstance(item_type, list) else [item_type]
            if "JobPosting" in types:
                return item
    return None


def parse_next_data(html: str) -> dict[str, Any] | None:
    """Extract and parse the Next.js ``__NEXT_DATA__`` JSON embedded in *html*."""
    pattern = re.compile(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(html)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    return None


def parse_page_title(html: str) -> str:
    """Extract the ``<title>`` tag content from *html*."""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if match:
        return clean_description(match.group(1))
    return ""


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def normalize_salary(value: Any) -> float | None:
    """Convert salary value to float; return None for invalid/missing values."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, str):
        # Strip everything except digits (handle spaces as thousands separators)
        digits = re.sub(r"[^\d]", "", value)
        try:
            result = float(digits)
            return result if result > 0 else None
        except ValueError:
            return None
    return None


def normalize_currency(value: Any) -> str:
    """Return an uppercase currency code; default to PLN."""
    if not value:
        return "PLN"
    upper = str(value).upper().strip()
    return upper if upper in {"PLN", "EUR", "USD", "GBP", "CHF", "CZK", "HUF", "DKK", "SEK", "NOK"} else "PLN"


_CONTRACT_MAP: list[tuple[list[str], str]] = [
    # "any" patterns must come before individual type patterns to avoid false matches
    (["any", "both", "b2b + uop", "b2b+uop", "b2b/uop"], "Any"),
    (["b2b", "business-to-business", "samozatrudnienie", "contractor"], "B2B"),
    (["umowa o pracę", "umowa o prace", "uop", "permanent", "full_time", "full-time", "employment contract"], "UoP"),
    (["zlecenie", "mandate", "temporary", "contract_of_mandate", "contract of mandate"], "Contract"),
    (["intern", "staż", "staz", "apprentice"], "Internship"),
    (["freelance", "self-employed"], "B2B"),
]


def normalize_contract(value: Any) -> str:
    """Return a normalized contract type string."""
    if not value:
        return ""
    text = str(value).lower().strip()
    for keywords, normalized in _CONTRACT_MAP:
        if any(kw in text for kw in keywords):
            return normalized
    # Schema.org FULL_TIME → UoP
    if text in {"full_time", "fulltime"}:
        return "UoP"
    if text in {"contractor", "b2b"}:
        return "B2B"
    return str(value).strip()


def normalize_technologies(items: Any) -> list[str]:
    """Deduplicate a list of technology strings (case-insensitive, preserve first casing)."""
    if not items:
        return []
    raw: list[str] = []
    if isinstance(items, str):
        # comma-separated string
        raw = [t.strip() for t in items.split(",") if t.strip()]
    elif isinstance(items, list):
        for item in items:
            if isinstance(item, str):
                raw.append(item.strip())
            elif isinstance(item, dict):
                name = item.get("name") or item.get("title") or item.get("skill") or ""
                if name:
                    raw.append(str(name).strip())
    seen_lower: set[str] = set()
    result: list[str] = []
    for tech in raw:
        if not tech:
            continue
        if tech.lower() not in seen_lower:
            seen_lower.add(tech.lower())
            result.append(tech)
    return result


# ---------------------------------------------------------------------------
# JSON-LD JobPosting field extraction
# ---------------------------------------------------------------------------


def extract_json_ld_salary(data: dict[str, Any]) -> tuple[float | None, float | None, str]:
    """Extract min/max salary and currency from a JSON-LD ``baseSalary`` value."""
    sal = data.get("baseSalary")
    if not sal:
        return None, None, "PLN"
    currency = normalize_currency(sal.get("currency"))
    value = sal.get("value")
    if isinstance(value, dict):
        sal_min = normalize_salary(value.get("minValue") or value.get("value"))
        sal_max = normalize_salary(value.get("maxValue") or value.get("value"))
        return sal_min, sal_max, currency
    if isinstance(value, (int, float)):
        v = normalize_salary(value)
        return v, v, currency
    return None, None, currency


def extract_json_ld_location(data: dict[str, Any]) -> str:
    """Extract a human-readable location from JSON-LD ``jobLocation``."""
    loc = data.get("jobLocation")
    if not loc:
        return ""
    if isinstance(loc, list):
        loc = loc[0]
    if isinstance(loc, dict):
        addr = loc.get("address") or loc
        if isinstance(addr, dict):
            return (
                addr.get("addressLocality")
                or addr.get("addressRegion")
                or addr.get("addressCountry")
                or ""
            )
        if isinstance(addr, str):
            return addr
    return ""


def draft_from_json_ld(data: dict[str, Any]) -> OfferDraft:
    """Populate an :class:`OfferDraft` from a JSON-LD JobPosting dict."""
    draft = OfferDraft()
    draft.extraction_source = JSON_LD

    draft.title = str(data.get("title") or data.get("name") or "").strip()
    org = data.get("hiringOrganization") or data.get("organization") or {}
    if isinstance(org, dict):
        draft.company = str(org.get("name") or "").strip()
    draft.location = extract_json_ld_location(data)
    draft.description = clean_description(str(data.get("description") or ""))
    draft.salary_min, draft.salary_max, draft.currency = extract_json_ld_salary(data)

    emp_type = data.get("employmentType") or ""
    if isinstance(emp_type, list):
        emp_type = emp_type[0] if emp_type else ""
    draft.contract = normalize_contract(emp_type)

    skills_raw = data.get("skills") or data.get("qualifications") or []
    draft.technologies = normalize_technologies(skills_raw)

    # Count confidence from populated fields
    draft.extraction_confidence = draft._filled_count() / 5
    return draft
