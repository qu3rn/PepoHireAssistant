"""High-level service layer for the Playwright-based job-URL collection pipeline.

Usage example::

    from cv_sender.playwright_collection import collect_and_import
    from cv_sender.config import load_settings
    from cv_sender.collectors.base import JobSearchCriteria

    criteria = JobSearchCriteria.from_config(load_settings().job_search)
    cfg = load_settings().playwright_collection
    result = collect_and_import(
        criteria,
        sources=["justjoin", "rocketjobs"],
        cfg=cfg,
        auto_score=True,
    )
    print(result)
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cv_sender.collector_diagnostics import CollectionDiagnostics, SourceSummary, save_collection_diagnostics
from cv_sender.collectors.base import JobCollectionResult
from cv_sender.collectors.playwright_base import PlaywrightCollectionResult

if TYPE_CHECKING:
    from cv_sender.collectors.base import JobSearchCriteria
    from cv_sender.config import PlaywrightCollectionConfig

logger = logging.getLogger(__name__)

# Registry mapping source name → collector class (lazy import to avoid hard
# dependency on playwright at import time)
_COLLECTOR_REGISTRY: dict[str, type] = {}


def import_offers_from_urls(*args, **kwargs):
    """Local wrapper kept at module scope so tests and callers can patch the import step."""
    from cv_sender.services import import_offers_from_urls as _import_offers_from_urls  # noqa: PLC0415

    return _import_offers_from_urls(*args, **kwargs)


def _get_collector_class(source: str) -> type | None:
    """Return the Playwright collector class for *source*, or None if unknown."""
    if not _COLLECTOR_REGISTRY:
        # Populate on first use
        from cv_sender.collectors.playwright_justjoin import PlaywrightJustJoinCollector  # noqa: PLC0415
        from cv_sender.collectors.playwright_linkedin import PlaywrightLinkedInCollector  # noqa: PLC0415
        from cv_sender.collectors.playwright_nofluffjobs import PlaywrightNoFluffJobsCollector  # noqa: PLC0415
        from cv_sender.collectors.playwright_pracuj import PlaywrightPracujCollector  # noqa: PLC0415
        from cv_sender.collectors.playwright_rocketjobs import PlaywrightRocketJobsCollector  # noqa: PLC0415

        _COLLECTOR_REGISTRY.update({
            "justjoin": PlaywrightJustJoinCollector,
            "rocketjobs": PlaywrightRocketJobsCollector,
            "nofluffjobs": PlaywrightNoFluffJobsCollector,
            "pracuj": PlaywrightPracujCollector,
            "linkedin": PlaywrightLinkedInCollector,
        })
    return _COLLECTOR_REGISTRY.get(source)


def collect_job_urls_with_playwright(
    criteria: JobSearchCriteria,
    sources: list[str],
    cfg: PlaywrightCollectionConfig,
    custom_listing_urls: dict[str, list[str]] | None = None,
) -> list[PlaywrightCollectionResult]:
    """Compatibility wrapper exposing the explicit Playwright collection API name."""
    return collect_job_urls(criteria, sources, cfg, custom_listing_urls)


def import_collected_urls(
    result: PlaywrightCollectionResult,
    auto_score: bool = True,
    *,
    add_to_queue: bool = False,
    attach_to_active_campaigns: bool = False,
) -> JobCollectionResult:
    """Import URLs collected from one source and optionally refresh queue/campaign attachments."""
    imported = JobCollectionResult(
        source=result.source,
        raw_found_count=result.raw_link_count,
        collected_count=result.job_url_count,
    )
    imported.errors.extend(result.errors)

    urls = [item.url for item in result.collected_urls]
    if not urls:
        return imported

    batch_result = import_offers_from_urls(
        urls=urls,
        auto_score=auto_score,
        max_urls=max(len(urls), 50),
    )
    imported.imported_count = batch_result.imported_count
    imported.duplicate_count = batch_result.duplicate_count + result.duplicate_count
    imported.failed_count = batch_result.failed_count

    for item in batch_result.items:
        if item.error:
            imported.errors.append(f"{item.url}: {item.error}")

    if add_to_queue and batch_result.imported_count:
        try:
            from cv_sender.apply_queue import build_apply_queue_from_offers  # noqa: PLC0415

            build_apply_queue_from_offers()
        except Exception as exc:  # noqa: BLE001
            imported.errors.append(f"Queue rebuild failed: {exc}")

    if attach_to_active_campaigns and batch_result.imported_count:
        try:
            from cv_sender.campaigns import build_campaign_queue, get_active_campaigns  # noqa: PLC0415

            for campaign in get_active_campaigns():
                build_campaign_queue(campaign.id)
        except Exception as exc:  # noqa: BLE001
            imported.errors.append(f"Campaign queue rebuild failed: {exc}")

    return imported


def collect_import_and_score_with_playwright(
    criteria: JobSearchCriteria,
    sources: list[str],
    cfg: PlaywrightCollectionConfig,
    *,
    custom_listing_urls: dict[str, list[str]] | None = None,
    collect_urls_only: bool = False,
    score_after_import: bool = True,
    add_to_queue: bool = False,
    attach_to_active_campaigns: bool = False,
) -> CollectionDiagnostics:
    """Collect public job URLs with Playwright, optionally import them, and persist diagnostics."""
    collection_results = collect_job_urls(criteria, sources, cfg, custom_listing_urls)
    started_at = min((result.started_at for result in collection_results), default=datetime.now(UTC))
    finished_at = max((result.finished_at or datetime.now(UTC) for result in collection_results), default=datetime.now(UTC))

    source_summaries: list[SourceSummary] = []
    global_warnings: list[str] = []

    for result in collection_results:
        import_summary = import_collected_urls(
            result,
            auto_score=score_after_import,
            add_to_queue=add_to_queue,
            attach_to_active_campaigns=attach_to_active_campaigns,
        ) if not collect_urls_only else JobCollectionResult(
            source=result.source,
            raw_found_count=result.raw_link_count,
            collected_count=result.job_url_count,
            duplicate_count=result.duplicate_count,
        )

        status = "ok"
        error = ""
        if result.errors:
            status = "failed" if not result.collected_urls else "partial"
            error = "; ".join(result.errors)
        elif result.warnings:
            status = "partial"
            error = "; ".join(result.warnings)
        elif result.raw_link_count == 0:
            status = "partial"
            error = "source returned 0 raw links"

        source_summaries.append(
            SourceSummary(
                source=result.source,
                status=status,
                raw_found_count=result.raw_link_count,
                found_count=result.job_url_count,
                accepted_count=import_summary.imported_count if not collect_urls_only else result.job_url_count,
                duplicate_count=import_summary.duplicate_count,
                rejected_count=max(result.raw_link_count - result.job_url_count, 0),
                failed_count=import_summary.failed_count,
                error=error,
                duration_seconds=round(((result.finished_at or finished_at) - result.started_at).total_seconds(), 2),
            )
        )
        global_warnings.extend(result.warnings)
        if import_summary.errors:
            global_warnings.extend(import_summary.errors)

    report = CollectionDiagnostics(
        run_id=str(uuid.uuid4()),
        started_at=started_at,
        finished_at=finished_at,
        criteria={
            "keywords": list(criteria.keywords),
            "technologies": list(criteria.technologies),
            "locations": list(criteria.locations),
            "seniority": list(criteria.seniority),
            "contract_types": list(criteria.contract_types),
            "min_salary_b2b": criteria.min_salary_b2b,
            "require_salary": criteria.require_salary,
            "exclude_keywords": list(criteria.exclude_keywords),
            "mode": "playwright_collect_only" if collect_urls_only else "playwright_collect_import",
            "sources": list(sources),
        },
        source_summaries=source_summaries,
        global_warnings=global_warnings,
    )
    save_collection_diagnostics(report)
    return report


def collect_job_urls(
    criteria: JobSearchCriteria,
    sources: list[str],
    cfg: PlaywrightCollectionConfig,
    custom_listing_urls: dict[str, list[str]] | None = None,
) -> list[PlaywrightCollectionResult]:
    """Open browser, scroll through job listing pages, and return collected URLs.

    Parameters
    ----------
    criteria:
        Search criteria used to build listing URLs.
    sources:
        Which sources to collect from.  Unknown sources produce an error result.
    cfg:
        Playwright collection configuration (headless, scrolls, etc.).
    custom_listing_urls:
        Optional per-source override listing URLs, e.g.
        ``{"justjoin": ["https://justjoin.it/job-offers/react"]}``.

    Returns
    -------
    list[PlaywrightCollectionResult]
        One result per source, in the same order as *sources*.
    """
    results: list[PlaywrightCollectionResult] = []

    for source in sources:
        cls = _get_collector_class(source)
        if cls is None:
            r = PlaywrightCollectionResult(source=source)
            r.errors.append(
                f"No Playwright collector available for source {source!r}. "
                f"Known sources: {sorted(_COLLECTOR_REGISTRY)}"
            )
            results.append(r)
            continue

        collector = cls()
        per_source_custom = (custom_listing_urls or {}).get(source)
        try:
            result = collector.collect_urls(criteria, cfg, custom_listing_urls=per_source_custom)
        except Exception as exc:  # noqa: BLE001
            result = PlaywrightCollectionResult(source=source)
            result.errors.append(f"Unhandled error in Playwright collector for {source}: {exc}")
            logger.exception("Playwright collector %s crashed", source)

        results.append(result)

    return results


def collect_and_import(
    criteria: JobSearchCriteria,
    sources: list[str],
    cfg: PlaywrightCollectionConfig,
    auto_score: bool = True,
    custom_listing_urls: dict[str, list[str]] | None = None,
) -> dict:
    """Collect URLs, import them, optionally score, and return a summary dict.

    Returns a dict with keys:
    - ``collection_results``: list of PlaywrightCollectionResult
    - ``total_collected``: int
    - ``total_imported``: int
    - ``total_duplicates``: int
    - ``total_failed``: int
    - ``import_result``: BatchImportResult or None
    - ``errors``: list[str]
    """
    collection_results = collect_job_urls(criteria, sources, cfg, custom_listing_urls)

    all_urls: list[str] = []
    for r in collection_results:
        all_urls.extend(cu.url for cu in r.collected_urls)

    # Deduplicate before import
    seen: set[str] = set()
    unique_urls: list[str] = []
    for url in all_urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)

    total_collected = len(unique_urls)
    import_result = None
    errors: list[str] = []

    for r in collection_results:
        errors.extend(r.errors)

    if unique_urls:
        try:
            import_result = import_offers_from_urls(
                urls=unique_urls,
                auto_score=auto_score,
                max_urls=max(len(unique_urls), 50),
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Import failed: {exc}")
            logger.exception("Playwright import step failed")

    total_imported = import_result.imported_count if import_result else 0
    total_duplicates = (import_result.duplicate_count if import_result else 0) + sum(
        r.duplicate_count for r in collection_results
    )
    total_failed = import_result.failed_count if import_result else 0

    return {
        "collection_results": collection_results,
        "total_collected": total_collected,
        "total_imported": total_imported,
        "total_duplicates": total_duplicates,
        "total_failed": total_failed,
        "import_result": import_result,
        "errors": errors,
    }
