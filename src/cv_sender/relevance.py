"""Language-aware offer relevance matching.

This module is intentionally separate from URL classification:
- URL classification answers: "is this an individual job-offer page?"
- Relevance matching answers: "is this offer relevant to my search criteria?"
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from cv_sender.collectors.base import JobSearchCriteria

RelevanceDecision = Literal["relevant", "needs_review", "irrelevant"]


class RelevanceResult(BaseModel):
    is_relevant: bool = False
    score: int = 0
    matched_keywords: list[str] = Field(default_factory=list)
    matched_technologies: list[str] = Field(default_factory=list)
    matched_languages: list[str] = Field(default_factory=list)
    rejected_keywords: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    decision: RelevanceDecision = "irrelevant"


@dataclass
class EmergencyReactMode:
    enabled: bool = False
    accept_needs_review: bool = True
    reject_obvious_non_it: bool = True
    min_relevance_score: int = 50
    needs_review_score: int = 25


_FRONTEND_SYNONYMS_EN = [
    "frontend",
    "front-end",
    "front end",
    "frontend developer",
    "front-end developer",
    "frontend engineer",
    "ui developer",
    "web developer",
]

_FRONTEND_SYNONYMS_PL = [
    "programista frontend",
    "programista front-end",
    "programista front end",
    "developer frontend",
    "inżynier frontend",
    "inzynier frontend",
]

_REACT_SYNONYMS = [
    "react",
    "react.js",
    "reactjs",
    "react developer",
    "programista react",
]

_TYPESCRIPT_SYNONYMS = [
    "typescript",
    "type script",
    " ts ",
]

_JAVASCRIPT_SYNONYMS = [
    "javascript",
    "java script",
    " js ",
]

_NEXT_SYNONYMS = [
    "next.js",
    "nextjs",
    " next ",
]

_NEGATIVE_STACK = [
    "angular",
    "vue",
    "php",
    "wordpress",
    "prestashop",
    "magento",
    " java ",
    ".net",
    "c#",
]

_NEGATIVE_NON_IT = [
    "sales",
    "sprzedawca",
    "kasjer",
    "magazynier",
    "operator",
    "kierowca",
    "call center",
]

_IT_FOCUSED_SOURCES = {"justjoin", "rocketjobs", "nofluffjobs"}


def _normalize(text: str) -> str:
    return f" {text.lower()} "


def _contains_any(text: str, terms: list[str]) -> list[str]:
    t = _normalize(text)
    hits: list[str] = []
    for term in terms:
        if term in t and term not in hits:
            hits.append(term.strip())
    return hits


def _extract_texts(collected_url_or_offer: Any) -> tuple[str, str, str, str, list[str], str]:
    """Return (url, title, preview, description, technologies, source)."""
    if hasattr(collected_url_or_offer, "url"):
        url = getattr(collected_url_or_offer, "url", "") or ""
        title = getattr(collected_url_or_offer, "title", "") or getattr(collected_url_or_offer, "title_preview", "") or ""
        preview = getattr(collected_url_or_offer, "raw_text_preview", "") or ""
        description = getattr(collected_url_or_offer, "description", "") or ""
        technologies = list(getattr(collected_url_or_offer, "technologies", []) or [])
        source = getattr(collected_url_or_offer, "source", "") or ""
        return url, title, preview, description, technologies, source

    if isinstance(collected_url_or_offer, dict):
        return (
            str(collected_url_or_offer.get("url", "")),
            str(collected_url_or_offer.get("title", "") or collected_url_or_offer.get("title_preview", "")),
            str(collected_url_or_offer.get("raw_text_preview", "")),
            str(collected_url_or_offer.get("description", "")),
            list(collected_url_or_offer.get("technologies", []) or []),
            str(collected_url_or_offer.get("source", "")),
        )

    return "", "", "", "", [], ""


def _decision_from_score(score: int, mode: EmergencyReactMode) -> RelevanceDecision:
    if score >= mode.min_relevance_score:
        return "relevant"
    if score >= mode.needs_review_score:
        return "needs_review"
    return "irrelevant"


def match_offer_relevance(
    collected_url_or_offer: Any,
    criteria: JobSearchCriteria,
    *,
    emergency_mode: EmergencyReactMode | None = None,
) -> RelevanceResult:
    mode = emergency_mode or EmergencyReactMode()
    url, title, preview, description, technologies, source = _extract_texts(collected_url_or_offer)

    # URL-only stage can still score using slug/path and preview text.
    url_slug = re.sub(r"https?://[^/]+", "", url.lower())
    title_l = title.lower()
    preview_l = preview.lower()
    desc_l = description.lower()
    tech_blob = " ".join(technologies).lower()
    combined_short = f" {url_slug} {title_l} {preview_l} "
    combined_full = f" {combined_short} {desc_l} {tech_blob} "

    score = 0
    matched_keywords: list[str] = []
    matched_technologies: list[str] = []
    matched_languages: list[str] = []
    rejected_keywords: list[str] = []
    reasons: list[str] = []
    warnings: list[str] = []

    frontend_hits = _contains_any(combined_short, _FRONTEND_SYNONYMS_EN + _FRONTEND_SYNONYMS_PL)
    react_hits = _contains_any(combined_short, _REACT_SYNONYMS)
    ts_hits = _contains_any(combined_short, _TYPESCRIPT_SYNONYMS)
    js_hits = _contains_any(combined_short, _JAVASCRIPT_SYNONYMS)
    next_hits = _contains_any(combined_short, _NEXT_SYNONYMS)

    if react_hits:
        score += 50
        matched_technologies.extend(react_hits)
        reasons.append("react_signal_url_or_title")
    if frontend_hits:
        score += 35
        matched_keywords.extend(frontend_hits)
        reasons.append("frontend_signal_url_or_title")
    if ts_hits:
        score += 25
        matched_technologies.extend(ts_hits)
        reasons.append("typescript_signal_url_or_title")
    if js_hits:
        score += 20
        matched_technologies.extend(js_hits)
        reasons.append("javascript_signal_url_or_title")
    if next_hits:
        score += 15
        matched_technologies.extend(next_hits)
        reasons.append("nextjs_signal_url_or_title")

    if _contains_any(combined_full, ["react", "reactjs", "react.js"]):
        score += 10
        reasons.append("react_signal_description_or_preview")

    if source.lower() in _IT_FOCUSED_SOURCES:
        score += 10
        reasons.append("it_focused_source_bonus")

    non_it_hits = _contains_any(combined_short, _NEGATIVE_NON_IT)
    if non_it_hits:
        score -= 60
        rejected_keywords.extend(non_it_hits)
        reasons.append("obvious_non_it_terms")

    stack_hits = _contains_any(combined_short, _NEGATIVE_STACK)
    if stack_hits:
        score -= 30
        rejected_keywords.extend(stack_hits)
        reasons.append("strong_other_stack_terms")

    location_conflict = False
    if criteria.locations:
        loc_blob = f" {title_l} {preview_l} {desc_l} "
        if not any(loc.lower() in loc_blob for loc in criteria.locations):
            location_conflict = True
    if location_conflict:
        score -= 20
        reasons.append("location_conflict")

    if criteria.contract_types:
        contract_blob = f" {title_l} {preview_l} {desc_l} "
        if not any(c.lower() in contract_blob for c in criteria.contract_types):
            score -= 20
            reasons.append("contract_conflict")

    if criteria.keywords:
        kw_blob = f" {title_l} {preview_l} {desc_l} "
        for kw in criteria.keywords:
            if kw.lower() in kw_blob:
                matched_keywords.append(kw)

    if criteria.technologies:
        tech_text = f" {combined_full} "
        for tech in criteria.technologies:
            if tech.lower() in tech_text:
                matched_technologies.append(tech)

    if any(token in combined_full for token in ("programista", "sprzedawca", "kasjer", "magazynier", "kierowca")):
        matched_languages.append("pl")
    if any(token in combined_full for token in ("developer", "engineer", "frontend", "react")):
        matched_languages.append("en")

    if mode.enabled and mode.reject_obvious_non_it and non_it_hits:
        warnings.append("Emergency React mode rejected obvious non-IT signals.")

    if mode.enabled and stack_hits and react_hits:
        warnings.append("Mixed stack detected (React + non-target stack).")

    decision = _decision_from_score(score, mode)
    is_relevant = decision == "relevant"

    # De-duplicate lists while preserving order.
    def _dedupe(items: list[str]) -> list[str]:
        out: list[str] = []
        for item in items:
            if item and item not in out:
                out.append(item)
        return out

    return RelevanceResult(
        is_relevant=is_relevant,
        score=score,
        matched_keywords=_dedupe(matched_keywords),
        matched_technologies=_dedupe(matched_technologies),
        matched_languages=_dedupe(matched_languages),
        rejected_keywords=_dedupe(rejected_keywords),
        reasons=_dedupe(reasons),
        warnings=_dedupe(warnings),
        decision=decision,
    )
