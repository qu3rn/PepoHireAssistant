"""Job search service — orchestrates collectors and imports offers."""

from __future__ import annotations

import logging
from typing import Any

from cv_sender.collectors.base import (
    CollectedOffer,
    JobCollectionResult,
    JobSearchCriteria,
    passes_criteria_filter,
)
from cv_sender.models import ApplicationStatus, Decision, Offer
from cv_sender.storage import add_offer, load_applications, load_offers

logger = logging.getLogger(__name__)


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
