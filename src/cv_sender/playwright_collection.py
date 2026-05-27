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
from typing import TYPE_CHECKING

from cv_sender.collectors.playwright_base import PlaywrightCollectionResult

if TYPE_CHECKING:
    from cv_sender.collectors.base import JobSearchCriteria
    from cv_sender.config import PlaywrightCollectionConfig

logger = logging.getLogger(__name__)

# Registry mapping source name → collector class (lazy import to avoid hard
# dependency on playwright at import time)
_COLLECTOR_REGISTRY: dict[str, type] = {}


def _get_collector_class(source: str) -> type | None:
    """Return the Playwright collector class for *source*, or None if unknown."""
    if not _COLLECTOR_REGISTRY:
        # Populate on first use
        from cv_sender.collectors.playwright_justjoin import PlaywrightJustJoinCollector  # noqa: PLC0415
        from cv_sender.collectors.playwright_nofluffjobs import PlaywrightNoFluffJobsCollector  # noqa: PLC0415
        from cv_sender.collectors.playwright_pracuj import PlaywrightPracujCollector  # noqa: PLC0415
        from cv_sender.collectors.playwright_rocketjobs import PlaywrightRocketJobsCollector  # noqa: PLC0415

        _COLLECTOR_REGISTRY.update({
            "justjoin": PlaywrightJustJoinCollector,
            "rocketjobs": PlaywrightRocketJobsCollector,
            "nofluffjobs": PlaywrightNoFluffJobsCollector,
            "pracuj": PlaywrightPracujCollector,
        })
    return _COLLECTOR_REGISTRY.get(source)


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
    from cv_sender.services import import_offers_from_urls  # noqa: PLC0415

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
