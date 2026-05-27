"""Collector diagnostics — detailed per-offer decisions and collection run reports.

After a collection run, the caller can inspect what was found, what was imported,
what was skipped and *why*, and which sources failed.  The results are saved to
``data/collection_diagnostics.json`` (max 20 runs kept).
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from cv_sender.collectors.base import CollectedOffer, JobCollectionResult, JobSearchCriteria

logger = logging.getLogger(__name__)

_DEFAULT_DIAGNOSTICS_PATH = Path(
    os.getenv("COLLECTION_DIAGNOSTICS_PATH", "data/collection_diagnostics.json")
)
_MAX_RUNS = 20

# ---------------------------------------------------------------------------
# Reason codes
# ---------------------------------------------------------------------------

REASON_DUPLICATE_URL = "duplicate_url"
REASON_ALREADY_APPLIED = "already_applied"
REASON_MISSING_SALARY = "missing_salary"
REASON_SALARY_BELOW_MIN = "salary_below_minimum"
REASON_EXCLUDED_KEYWORD = "excluded_keyword"
REASON_NO_KEYWORD_MATCH = "no_required_keyword_match"
REASON_NO_TECH_MATCH = "no_required_technology_match"
REASON_WRONG_LOCATION = "wrong_location"
REASON_WRONG_SENIORITY = "wrong_seniority"
REASON_WRONG_CONTRACT = "wrong_contract"
REASON_IMPORT_FAILED = "import_failed"
REASON_LOW_SCORE = "low_score"
REASON_PROTECTED_PAGE = "protected_or_blocked_page"
REASON_LOGIN_REQUIRED = "login_required"
REASON_CAPTCHA = "captcha_detected"
REASON_UNKNOWN = "unknown_error"

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CollectedOfferDecision(BaseModel):
    """Per-offer decision with detailed reasons for acceptance or rejection."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    url: str = ""
    source: str = ""
    company: str = ""
    title: str = ""
    decision: Literal["accepted", "duplicate", "rejected", "failed", "needs_review"] = "rejected"
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    matched_keywords: list[str] = Field(default_factory=list)
    matched_technologies: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    salary_status: Literal["ok", "missing", "below_minimum", "unknown"] = "unknown"
    filter_score: int = 0  # rough quality score 0–100
    import_status: Literal["not_imported", "imported", "failed", "duplicate"] = "not_imported"
    offer_id: str | None = None
    error: str | None = None
    # Serialised CollectedOffer fields for "import anyway" action
    collected_data: dict = Field(default_factory=dict)
    manually_overridden: bool = False


class SourceSummary(BaseModel):
    """Per-source collection summary."""

    source: str
    status: Literal["ok", "partial", "failed"] = "ok"
    raw_found_count: int = 0    # items returned by the source API before local filtering
    found_count: int = 0        # items that passed local criteria filter
    accepted_count: int = 0
    duplicate_count: int = 0
    rejected_count: int = 0
    failed_count: int = 0
    error: str = ""
    duration_seconds: float = 0.0


class CollectionDiagnostics(BaseModel):
    """Full diagnostics report for one collection run."""

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    criteria: dict = Field(default_factory=dict)
    source_summaries: list[SourceSummary] = Field(default_factory=list)
    decisions: list[CollectedOfferDecision] = Field(default_factory=list)
    global_warnings: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)

    # Convenience totals
    @property
    def total_found(self) -> int:
        return sum(s.found_count for s in self.source_summaries)

    @property
    def total_accepted(self) -> int:
        return sum(s.accepted_count for s in self.source_summaries)

    @property
    def total_duplicates(self) -> int:
        return sum(s.duplicate_count for s in self.source_summaries)

    @property
    def total_rejected(self) -> int:
        return sum(s.rejected_count for s in self.source_summaries)

    @property
    def total_failed(self) -> int:
        return sum(s.failed_count for s in self.source_summaries)


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------


def _norm(text: str) -> str:
    return text.lower().strip()


def _matches_any(text: str, terms: list[str]) -> list[str]:
    """Return the terms from *terms* that appear in *text* (case-insensitive)."""
    t = _norm(text)
    return [term for term in terms if _norm(term) in t]


def evaluate_collected_offer(
    candidate: CollectedOffer,
    criteria: JobSearchCriteria,
    existing_urls: set[str],
    applied_urls: set[str],
) -> CollectedOfferDecision:
    """Evaluate one collected offer and return a detailed decision.

    Args:
        candidate:     The collected offer to evaluate.
        criteria:      Active search criteria.
        existing_urls: Set of URLs already stored as Offers (duplicates).
        applied_urls:  Set of offer URLs that have a submitted application.
    """
    reasons: list[str] = []
    warnings: list[str] = []
    salary_status: Literal["ok", "missing", "below_minimum", "unknown"] = "unknown"

    combined = (
        f"{candidate.title} {candidate.description_preview} "
        + " ".join(candidate.technologies)
    )

    # --- Duplicate check ---------------------------------------------------
    if candidate.url in existing_urls:
        return CollectedOfferDecision(
            url=candidate.url,
            source=candidate.source,
            company=candidate.company,
            title=candidate.title,
            decision="duplicate",
            reasons=[REASON_DUPLICATE_URL],
            salary_status=salary_status,
            import_status="duplicate",
            collected_data=_offer_to_dict(candidate),
        )

    if candidate.url in applied_urls:
        return CollectedOfferDecision(
            url=candidate.url,
            source=candidate.source,
            company=candidate.company,
            title=candidate.title,
            decision="duplicate",
            reasons=[REASON_ALREADY_APPLIED],
            salary_status=salary_status,
            import_status="duplicate",
            collected_data=_offer_to_dict(candidate),
        )

    # --- Excluded keyword check --------------------------------------------
    excluded_found: list[str] = []
    for kw in criteria.exclude_keywords:
        if _norm(kw) in _norm(combined):
            excluded_found.append(kw)
    if excluded_found:
        reasons.append(REASON_EXCLUDED_KEYWORD)

    # --- Keyword / tech match ---------------------------------------------
    matched_kws = _matches_any(
        candidate.title + " " + candidate.description_preview, criteria.keywords
    )
    matched_techs = _matches_any(combined, criteria.technologies)

    if criteria.keywords and not matched_kws:
        reasons.append(REASON_NO_KEYWORD_MATCH)
    if criteria.technologies and not matched_techs:
        reasons.append(REASON_NO_TECH_MATCH)

    # --- Salary checks -----------------------------------------------------
    has_salary = candidate.salary_min is not None or candidate.salary_max is not None

    if not has_salary:
        salary_status = "missing"
        if criteria.require_salary:
            reasons.append(REASON_MISSING_SALARY)
    elif criteria.min_salary_b2b > 0 and candidate.salary_min is not None:
        if candidate.salary_min < criteria.min_salary_b2b:
            salary_status = "below_minimum"
            reasons.append(REASON_SALARY_BELOW_MIN)
        else:
            salary_status = "ok"
    else:
        salary_status = "ok" if has_salary else "missing"

    # --- Filter score -------------------------------------------------------
    filter_score = _compute_filter_score(candidate, criteria, matched_kws, matched_techs)

    # --- Final decision -----------------------------------------------------
    if reasons and excluded_found:
        decision: Literal["accepted", "duplicate", "rejected", "failed", "needs_review"] = "rejected"
    elif REASON_MISSING_SALARY in reasons or REASON_SALARY_BELOW_MIN in reasons:
        decision = "rejected"
    elif REASON_NO_KEYWORD_MATCH in reasons and REASON_NO_TECH_MATCH in reasons:
        decision = "rejected"
    elif reasons:
        # has some warnings but not a hard fail — flag for review
        decision = "needs_review"
    else:
        decision = "accepted"

    if not matched_kws and not matched_techs:
        decision = "rejected"

    return CollectedOfferDecision(
        url=candidate.url,
        source=candidate.source,
        company=candidate.company,
        title=candidate.title,
        decision=decision,
        reasons=reasons,
        warnings=warnings,
        matched_keywords=matched_kws,
        matched_technologies=matched_techs,
        excluded_keywords=excluded_found,
        salary_status=salary_status,
        filter_score=filter_score,
        collected_data=_offer_to_dict(candidate),
    )


def _compute_filter_score(
    candidate: CollectedOffer,
    criteria: JobSearchCriteria,
    matched_kws: list[str],
    matched_techs: list[str],
) -> int:
    """Return a 0–100 score reflecting how well the offer matches the criteria."""
    score = 0
    checks = 0

    if criteria.keywords:
        checks += 1
        if matched_kws:
            score += 1
    if criteria.technologies:
        checks += 1
        if matched_techs:
            score += 1
    if criteria.require_salary:
        checks += 1
        if candidate.salary_min is not None:
            score += 1
    elif candidate.salary_min is not None:
        score += 1
        checks += 1

    return int(score / max(checks, 1) * 100)


def _offer_to_dict(offer: CollectedOffer) -> dict:
    return {
        "source": offer.source,
        "url": offer.url,
        "title": offer.title,
        "company": offer.company,
        "location": offer.location,
        "salary_min": offer.salary_min,
        "salary_max": offer.salary_max,
        "currency": offer.currency,
        "contract": offer.contract,
        "technologies": list(offer.technologies),
        "description_preview": offer.description_preview,
    }


# ---------------------------------------------------------------------------
# Suggestion generation
# ---------------------------------------------------------------------------


def generate_suggestions(
    decisions: list[CollectedOfferDecision],
    source_summaries: list[SourceSummary],
) -> list[str]:
    """Produce human-readable improvement suggestions based on rejection patterns."""
    suggestions: list[str] = []
    total = len(decisions)
    if total == 0:
        return ["No offers were collected. Check that the selected sources are reachable."]

    rejected = [d for d in decisions if d.decision in ("rejected", "needs_review")]
    r_total = len(rejected)

    # Salary filter too strict
    missing_sal = sum(1 for d in rejected if REASON_MISSING_SALARY in d.reasons)
    if missing_sal > 0 and missing_sal / total > 0.3:
        suggestions.append(
            f"{missing_sal}/{total} offers were rejected because salary is not visible. "
            "Consider unchecking 'Require salary visible'."
        )

    below_min = sum(1 for d in rejected if REASON_SALARY_BELOW_MIN in d.reasons)
    if below_min > 0 and below_min / total > 0.3:
        suggestions.append(
            f"{below_min}/{total} offers are below the salary minimum. "
            "Consider lowering the minimum or prioritising sources that show salary."
        )

    # Keyword match
    no_kw = sum(1 for d in rejected if REASON_NO_KEYWORD_MATCH in d.reasons)
    if no_kw > 0 and no_kw / total > 0.3:
        suggestions.append(
            f"{no_kw}/{total} offers did not match your keywords. "
            "Try adding broader terms like 'Frontend', 'Web Developer', or 'JavaScript'."
        )

    # Duplicates
    dupes = sum(1 for d in decisions if d.decision == "duplicate")
    if dupes > 0 and dupes / total > 0.4:
        suggestions.append(
            f"{dupes}/{total} offers were duplicates. "
            "Consider collecting from additional sources or waiting for fresh listings."
        )

    # Source failures
    for ss in source_summaries:
        if ss.status == "failed":
            suggestions.append(
                f"Source '{ss.source}' failed: {ss.error or 'unknown error'}. "
                "Use the Bookmarklet to manually import offers from this source."
            )
        elif ss.status == "partial" and ss.error:
            suggestions.append(
                f"Source '{ss.source}' returned partial results: {ss.error}"
            )
        elif ss.raw_found_count > 0 and ss.found_count == 0:
            suggestions.append(
                f"Source '{ss.source}' found {ss.raw_found_count} raw offers but all "
                "were rejected by local filters. Try broader keywords or lower the "
                "minimum salary."
            )

    return suggestions


# ---------------------------------------------------------------------------
# Main collection entry-point with diagnostics
# ---------------------------------------------------------------------------


def collect_with_diagnostics(
    criteria: JobSearchCriteria,
    source_names: list[str],
    auto_score: bool = True,
) -> CollectionDiagnostics:
    """Run collection and produce a full :class:`CollectionDiagnostics` report.

    Each source failure is isolated — one broken source does not stop others.
    Returns a rich report including per-offer decisions and improvement suggestions.
    """
    from cv_sender.job_search import _get_collector, _import_collected_offer  # noqa: PLC0415
    from cv_sender.storage import load_applications, load_offers  # noqa: PLC0415

    run_id = str(uuid.uuid4())
    started_at = datetime.now(UTC)

    # Pre-load existing state once
    existing_offers = load_offers()
    existing_urls: set[str] = {o.url for o in existing_offers}
    applications = load_applications()
    # Offer URLs that already have a submitted application
    _sent_like = {
        "sent", "follow_up_due", "follow_up_sent", "reply_received",
        "interview", "offer", "rejected", "no_response", "archived",
    }
    applied_urls: set[str] = set()
    for app in applications:
        if app.status in _sent_like:
            offer_obj = next((o for o in existing_offers if o.id == app.offer_id), None)
            if offer_obj:
                applied_urls.add(offer_obj.url)

    criteria_dict = {
        "keywords": list(criteria.keywords),
        "technologies": list(criteria.technologies),
        "locations": list(criteria.locations),
        "min_salary_b2b": criteria.min_salary_b2b,
        "require_salary": criteria.require_salary,
        "exclude_keywords": list(criteria.exclude_keywords),
    }

    all_decisions: list[CollectedOfferDecision] = []
    source_summaries: list[SourceSummary] = []
    total_imported = 0

    for name in source_names:
        if total_imported >= criteria.max_total_offers:
            break

        t0 = time.monotonic()
        ss = SourceSummary(source=name)

        collector = _get_collector(name)
        if collector is None:
            ss.status = "failed"
            ss.error = f"No collector registered for source {name!r}"
            ss.duration_seconds = round(time.monotonic() - t0, 2)
            source_summaries.append(ss)
            continue

        try:
            raw = collector.search(criteria)
        except Exception as exc:  # noqa: BLE001
            ss.status = "failed"
            ss.error = str(exc)
            ss.duration_seconds = round(time.monotonic() - t0, 2)
            source_summaries.append(ss)
            logger.error("Collector %s failed: %s", name, exc)
            continue

        ss.raw_found_count = len(raw)

        # Apply local criteria filter and separate raw from accepted.
        from cv_sender.collectors.base import passes_criteria_filter  # noqa: PLC0415

        filtered_raw: list = []
        filter_rejected = 0
        for offer in raw:
            skip_reason = passes_criteria_filter(offer, criteria)
            if skip_reason:
                filter_rejected += 1
            else:
                filtered_raw.append(offer)

        ss.found_count = len(filtered_raw)

        if ss.raw_found_count > 0 and ss.found_count == 0:
            logger.info(
                "Source %s: raw_found=%d but all %d rejected by local filter",
                name,
                ss.raw_found_count,
                filter_rejected,
            )

        for offer in raw:
            decision = evaluate_collected_offer(offer, criteria, existing_urls, applied_urls)

            if decision.decision == "duplicate":
                ss.duplicate_count += 1
                all_decisions.append(decision)
                continue

            if decision.decision in ("rejected", "failed"):
                ss.rejected_count += 1
                all_decisions.append(decision)
                continue

            if decision.decision == "needs_review":
                # Still try to import — it passed partial criteria
                ss.rejected_count += 1  # counted as not cleanly accepted
                all_decisions.append(decision)
                continue

            # decision == "accepted"
            if total_imported >= criteria.max_total_offers:
                decision.reasons.append("max_total_offers_reached")
                decision.decision = "rejected"
                ss.rejected_count += 1
                all_decisions.append(decision)
                continue

            try:
                outcome = _import_collected_offer(offer, criteria, auto_score)
            except Exception as exc:  # noqa: BLE001
                outcome = "failed"
                decision.error = str(exc)
                logger.error("Import failed for %s: %s", offer.url, exc)

            if outcome == "imported":
                decision.import_status = "imported"
                existing_urls.add(offer.url)  # prevent duplicate within same run
                total_imported += 1
                ss.accepted_count += 1
            elif outcome == "duplicate":
                decision.decision = "duplicate"
                decision.import_status = "duplicate"
                decision.reasons.insert(0, REASON_DUPLICATE_URL)
                ss.duplicate_count += 1
            elif outcome == "failed":
                decision.decision = "failed"
                decision.import_status = "failed"
                decision.reasons.append(REASON_IMPORT_FAILED)
                ss.failed_count += 1
            else:
                # "skipped" — criteria filter re-evaluated on import
                decision.decision = "rejected"
                decision.import_status = "not_imported"
                ss.rejected_count += 1

            all_decisions.append(decision)

        duration = round(time.monotonic() - t0, 2)
        ss.duration_seconds = duration

        if ss.status != "failed":
            if ss.failed_count > 0 and ss.accepted_count == 0:
                ss.status = "partial"
            elif ss.raw_found_count == 0:
                ss.status = "partial"
                if not ss.error:
                    ss.error = "source returned 0 raw results"
            else:
                ss.status = "ok"

        source_summaries.append(ss)

    suggestions = generate_suggestions(all_decisions, source_summaries)

    report = CollectionDiagnostics(
        run_id=run_id,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        criteria=criteria_dict,
        source_summaries=source_summaries,
        decisions=all_decisions,
        suggestions=suggestions,
    )

    save_collection_diagnostics(report)
    return report


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _read_diagnostics_file(path: Path) -> list[dict]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        return []
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []


def _write_diagnostics_file(path: Path, runs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(runs, fh, indent=2, ensure_ascii=False, default=str)


def save_collection_diagnostics(
    report: CollectionDiagnostics,
    path: Path | None = None,
) -> None:
    """Persist *report* to disk, pruning to the most recent ``_MAX_RUNS`` runs."""
    p = path or _DEFAULT_DIAGNOSTICS_PATH
    runs = _read_diagnostics_file(p)
    runs.append(report.model_dump(mode="json"))
    # Keep only latest N runs
    if len(runs) > _MAX_RUNS:
        runs = runs[-_MAX_RUNS:]
    _write_diagnostics_file(p, runs)


def get_latest_collection_diagnostics(
    path: Path | None = None,
) -> CollectionDiagnostics | None:
    """Return the most recent diagnostics run, or ``None`` if none saved."""
    p = path or _DEFAULT_DIAGNOSTICS_PATH
    runs = _read_diagnostics_file(p)
    if not runs:
        return None
    return CollectionDiagnostics.model_validate(runs[-1])


def get_collection_diagnostics(
    run_id: str,
    path: Path | None = None,
) -> CollectionDiagnostics | None:
    """Return the diagnostics run with *run_id*, or ``None``."""
    p = path or _DEFAULT_DIAGNOSTICS_PATH
    runs = _read_diagnostics_file(p)
    for run in runs:
        if run.get("run_id") == run_id:
            return CollectionDiagnostics.model_validate(run)
    return None


def list_diagnostic_run_ids(path: Path | None = None) -> list[str]:
    """Return all stored run_ids, most recent last."""
    p = path or _DEFAULT_DIAGNOSTICS_PATH
    runs = _read_diagnostics_file(p)
    return [r.get("run_id", "") for r in runs]


# ---------------------------------------------------------------------------
# Force import (import a rejected offer bypassing criteria)
# ---------------------------------------------------------------------------


def force_import_collected_offer(
    decision: CollectedOfferDecision,
    auto_score: bool = True,
    add_to_queue: bool = False,
) -> tuple[bool, str]:
    """Import a rejected/duplicate offer, bypassing criteria filters.

    Args:
        decision:     The :class:`CollectedOfferDecision` to force-import.
        auto_score:   Whether to score the offer after import.
        add_to_queue: Whether to add the imported offer to the rapid-apply queue.

    Returns:
        ``(success, message)``
    """
    from cv_sender.models import Offer  # noqa: PLC0415
    from cv_sender.storage import add_offer, update_offer  # noqa: PLC0415

    d = decision.collected_data
    if not d.get("url"):
        return False, "Decision has no URL — cannot force import."

    offer = Offer(
        source=d.get("source", ""),
        url=d["url"],
        title=d.get("title", decision.title),
        company=d.get("company", decision.company),
        location=d.get("location", ""),
        salary_min=d.get("salary_min"),
        salary_max=d.get("salary_max"),
        currency=d.get("currency", "PLN"),
        contract=d.get("contract", ""),
        technologies=list(d.get("technologies", [])),
        description=d.get("description_preview", ""),
    )

    saved = add_offer(offer)
    if not saved:
        return False, f"Offer already exists: {offer.url}"

    if auto_score:
        try:
            from cv_sender.config import load_settings  # noqa: PLC0415
            from cv_sender.llm import get_llm_score  # noqa: PLC0415
            from cv_sender.scorer import score_offer  # noqa: PLC0415

            settings = load_settings()
            llm_result = None
            if settings.lm_studio.enabled:
                try:
                    llm_result = get_llm_score(
                        offer_data=offer.model_dump(mode="json"),
                        criteria_data=settings.model_dump(mode="json"),
                        config=settings.lm_studio,
                    )
                except Exception:  # noqa: BLE001
                    pass
            scored = score_offer(offer, settings, llm_result)
            update_offer(scored)
            offer = scored
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scoring skipped after force import: %s", exc)

    if add_to_queue:
        try:
            from cv_sender.apply_queue import build_apply_queue_from_offers  # noqa: PLC0415

            build_apply_queue_from_offers()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Queue rebuild after force import failed: %s", exc)

    return True, f"Force-imported: {offer.title} @ {offer.company} ({offer.url})"
