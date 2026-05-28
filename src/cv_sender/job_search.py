"""Job search service — orchestrates collectors and imports offers."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Literal
from typing import Any

from cv_sender.collector_diagnostics import CollectionDiagnostics, SourceSummary, save_collection_diagnostics
from cv_sender.collectors.base import (
    CollectedOffer,
    JobCollectionResult,
    JobSearchCriteria,
    passes_criteria_filter,
)
from cv_sender.playwright_collection import collect_job_urls, import_collected_urls
from cv_sender.models import ApplicationStatus, Decision, Offer
from cv_sender.storage import add_offer, load_applications, load_offers

logger = logging.getLogger(__name__)

CollectorMode = Literal["playwright", "api", "static", "hybrid", "api_static"]


def _normalize_collector_mode(mode: str | None) -> str:
    val = (mode or "").strip().lower()
    if val in ("", "playwright"):
        return "playwright"
    if val in ("api", "static", "api_static"):
        return "api_static"
    if val == "hybrid":
        return "hybrid"
    return "playwright"


# ---------------------------------------------------------------------------
# Collector registry
# ---------------------------------------------------------------------------


def _get_collector(name: str):
    """Return an instantiated collector for *name*, or ``None``."""
    name = name.lower()
    try:
        if name == "justjoin":
            from cv_sender.collectors.justjoin import JustJoinCollector  # noqa: PLC0415

            return JustJoinCollector()
        if name == "rocketjobs":
            from cv_sender.collectors.rocketjobs import RocketJobsCollector  # noqa: PLC0415

            return RocketJobsCollector()
        if name == "nofluffjobs":
            from cv_sender.collectors.nofluffjobs import NoFluffJobsCollector  # noqa: PLC0415

            return NoFluffJobsCollector()
        if name == "pracuj":
            from cv_sender.collectors.pracuj import PracujCollector  # noqa: PLC0415

            return PracujCollector()
        if name == "linkedin":
            from cv_sender.collectors.linkedin import LinkedInCollector  # noqa: PLC0415

            return LinkedInCollector()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to instantiate collector %r: %s", name, exc)
    return None


# ---------------------------------------------------------------------------
# Import helper
# ---------------------------------------------------------------------------


def _import_collected_offer(
    collected: CollectedOffer,
    criteria: JobSearchCriteria,
    auto_score: bool = True,
) -> str:
    """Import a single :class:`CollectedOffer` into the offer store.

    Returns one of: ``"imported"``, ``"duplicate"``, ``"skipped"``, ``"failed"``.
    """
    # Apply criteria filter
    skip_reason = passes_criteria_filter(collected, criteria)
    if skip_reason:
        return "skipped"

    offer = Offer(
        source=collected.source,
        url=collected.url,
        title=collected.title,
        company=collected.company,
        location=collected.location,
        salary_min=collected.salary_min,
        salary_max=collected.salary_max,
        currency=collected.currency,
        contract=collected.contract,
        technologies=collected.technologies,
        description=collected.description_preview,
    )

    saved = add_offer(offer)
    if not saved:
        return "duplicate"

    if auto_score:
        try:
            from cv_sender.config import load_settings  # noqa: PLC0415
            from cv_sender.llm import get_llm_score  # noqa: PLC0415
            from cv_sender.scorer import score_offer  # noqa: PLC0415
            from cv_sender.storage import update_offer  # noqa: PLC0415

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
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scoring failed for %s: %s", offer.url, exc)

    return "imported"


# ---------------------------------------------------------------------------
# Main collection entry-point
# ---------------------------------------------------------------------------


def run_job_collection(
    criteria: JobSearchCriteria,
    source_names: list[str],
    auto_score: bool = True,
) -> list[JobCollectionResult]:
    """Run collection across all *source_names* and import the results.

    Each source failure is caught independently so other sources continue.

    Args:
        criteria: Search parameters controlling keywords, locations, etc.
        source_names: List of source names to use (e.g. ``["justjoin", "rocketjobs"]``).
        auto_score: Whether to score each new offer with LLM + heuristic scorer.

    Returns:
        One :class:`JobCollectionResult` per source.
    """
    results: list[JobCollectionResult] = []
    total_imported = 0

    for name in source_names:
        if total_imported >= criteria.max_total_offers:
            logger.info("Max total offers (%d) reached, stopping.", criteria.max_total_offers)
            break

        collector = _get_collector(name)
        if collector is None:
            result = JobCollectionResult(source=name)
            result.errors.append(f"No collector found for source {name!r}")
            results.append(result)
            continue

        result = collector.collect_and_filter(criteria)
        results.append(result)

        # Import each non-filtered offer
        imported = 0
        duplicate = 0
        skipped = 0
        failed = 0

        for collected in result.offers:
            if collected.skip_reason:
                skipped += 1
                continue
            if total_imported >= criteria.max_total_offers:
                skipped += 1
                continue
            try:
                outcome = _import_collected_offer(collected, criteria, auto_score)
                if outcome == "imported":
                    imported += 1
                    total_imported += 1
                elif outcome == "duplicate":
                    collected.is_duplicate = True
                    duplicate += 1
                elif outcome == "skipped":
                    skipped += 1
                else:
                    failed += 1
            except Exception as exc:  # noqa: BLE001
                logger.error("Import failed for %s: %s", collected.url, exc)
                failed += 1

        result.imported_count = imported
        result.duplicate_count = duplicate
        result.skipped_count = skipped
        result.failed_count = failed

    return results


def run_job_collection_from_config(auto_score: bool = True) -> list[JobCollectionResult]:
    """Run collection using settings from the config file."""
    from cv_sender.config import load_settings  # noqa: PLC0415

    settings = load_settings()
    cfg = settings.job_search

    if not cfg.enabled:
        logger.info("Job search is disabled in settings.")
        return []

    criteria = JobSearchCriteria.from_config(cfg)
    enabled_sources = [
        name
        for name, src_cfg in cfg.sources.items()
        if src_cfg.enabled
    ]

    return run_job_collection(criteria, enabled_sources, auto_score)


def _results_to_api_summaries(results: list[JobCollectionResult], collector_used: str) -> tuple[list[SourceSummary], list[str]]:
    summaries: list[SourceSummary] = []
    warnings: list[str] = []
    for r in results:
        error = "; ".join(r.errors)
        status: Literal["ok", "partial", "failed"] = "ok"
        if r.errors:
            status = "failed" if r.imported_count == 0 else "partial"
        elif r.raw_found_count == 0:
            status = "partial"
            error = error or "source returned 0 raw results"

        summaries.append(
            SourceSummary(
                source=r.source,
                collector_used=collector_used,
                status=status,
                raw_found_count=r.raw_found_count,
                job_offer_url_count=r.collected_count,
                found_count=r.collected_count,
                imported_count=r.imported_count,
                skipped_count=r.skipped_count,
                accepted_count=r.imported_count,
                duplicate_count=r.duplicate_count,
                rejected_count=r.skipped_count,
                failed_count=r.failed_count,
                error=error,
                duration_seconds=0.0,
            )
        )
        warnings.extend(r.errors)
    return summaries, warnings


def collect_jobs(
    criteria: JobSearchCriteria,
    mode: CollectorMode | str | None = None,
    *,
    source_names: list[str] | None = None,
    auto_score: bool = True,
) -> CollectionDiagnostics:
    """Collect jobs using configured mode and return unified diagnostics."""
    from cv_sender.config import load_settings  # noqa: PLC0415

    settings = load_settings()
    cfg = settings.job_search
    resolved_mode = _normalize_collector_mode(mode or getattr(cfg, "collector_mode", "playwright"))
    active_sources = list(source_names) if source_names else [n for n, s in cfg.sources.items() if s.enabled]

    started_at = datetime.now(UTC)
    criteria_payload = {
        "keywords": list(criteria.keywords),
        "technologies": list(criteria.technologies),
        "locations": list(criteria.locations),
        "seniority": list(criteria.seniority),
        "contract_types": list(criteria.contract_types),
        "min_salary_b2b": criteria.min_salary_b2b,
        "require_salary": criteria.require_salary,
        "exclude_keywords": list(criteria.exclude_keywords),
        "collector_mode": resolved_mode,
        "sources": list(active_sources),
    }

    if not active_sources:
        report = CollectionDiagnostics(
            run_id=str(uuid.uuid4()),
            started_at=started_at,
            finished_at=datetime.now(UTC),
            criteria=criteria_payload,
            global_warnings=["No sources enabled for collection."],
            suggestions=["Enable at least one source in settings or pass --source."],
        )
        save_collection_diagnostics(report)
        return report

    if resolved_mode == "playwright":
        from cv_sender.playwright_collection import collect_import_and_score_with_playwright  # noqa: PLC0415

        report = collect_import_and_score_with_playwright(
            criteria,
            active_sources,
            settings.playwright_collection,
            collect_urls_only=False,
            score_after_import=auto_score,
        )
        return report

    if resolved_mode == "api_static":
        api_results = run_job_collection(criteria, active_sources, auto_score=auto_score)
        api_summaries, warnings = _results_to_api_summaries(api_results, collector_used="api/static")
        report = CollectionDiagnostics(
            run_id=str(uuid.uuid4()),
            started_at=started_at,
            finished_at=datetime.now(UTC),
            criteria=criteria_payload,
            source_summaries=api_summaries,
            global_warnings=warnings,
        )
        save_collection_diagnostics(report)
        return report

    # hybrid mode
    api_results = run_job_collection(criteria, active_sources, auto_score=auto_score)
    api_by_source = {r.source: r for r in api_results}
    fallback_enabled = bool(getattr(cfg, "fallback_to_playwright", True))
    fallback_sources: list[str] = []
    if fallback_enabled:
        for src in active_sources:
            r = api_by_source.get(src)
            if r is None or r.raw_found_count == 0 or r.imported_count == 0:
                fallback_sources.append(src)

    source_summaries: list[SourceSummary] = []
    global_warnings: list[str] = []

    fallback_map: dict[str, SourceSummary] = {}
    if fallback_sources:
        pw_results = collect_job_urls(criteria, fallback_sources, settings.playwright_collection)
        for pw_result in pw_results:
            import_summary = import_collected_urls(
                pw_result,
                auto_score=auto_score,
                criteria=criteria,
            )

            status: Literal["ok", "partial", "failed"] = "ok"
            error = ""
            if pw_result.errors:
                status = "failed" if pw_result.job_url_count == 0 else "partial"
                error = "; ".join(pw_result.errors)
            elif pw_result.warnings:
                status = "partial"
                error = "; ".join(pw_result.warnings)
            elif pw_result.raw_link_count == 0:
                status = "partial"
                error = "source returned 0 raw links"

            fallback_map[pw_result.source] = SourceSummary(
                source=pw_result.source,
                collector_used="hybrid_playwright_fallback",
                status=status,
                raw_found_count=pw_result.raw_link_count,
                job_offer_url_count=pw_result.job_url_count,
                found_count=pw_result.job_url_count,
                imported_count=import_summary.imported_count,
                skipped_count=max(import_summary.skipped_count, pw_result.raw_link_count - pw_result.job_url_count),
                accepted_count=import_summary.imported_count,
                duplicate_count=import_summary.duplicate_count,
                rejected_count=max(pw_result.raw_link_count - pw_result.job_url_count, 0),
                failed_count=import_summary.failed_count,
                error=error,
                duration_seconds=round(((pw_result.finished_at or datetime.now(UTC)) - pw_result.started_at).total_seconds(), 2),
            )

            global_warnings.extend(pw_result.warnings)
            global_warnings.extend(import_summary.errors)
            global_warnings.append(f"{pw_result.source}: API/static collector returned 0; Playwright fallback was used.")

    for src in active_sources:
        if src in fallback_map:
            source_summaries.append(fallback_map[src])
            continue
        api_res = api_by_source.get(src)
        if api_res is None:
            source_summaries.append(
                SourceSummary(
                    source=src,
                    collector_used="hybrid_api",
                    status="failed",
                    error="API/static collector did not return results.",
                )
            )
            continue

        api_summary = _results_to_api_summaries([api_res], collector_used="hybrid_api")[0][0]
        source_summaries.append(api_summary)
        global_warnings.extend(api_res.errors)

    report = CollectionDiagnostics(
        run_id=str(uuid.uuid4()),
        started_at=started_at,
        finished_at=datetime.now(UTC),
        criteria=criteria_payload,
        source_summaries=source_summaries,
        global_warnings=global_warnings,
    )
    save_collection_diagnostics(report)
    return report
