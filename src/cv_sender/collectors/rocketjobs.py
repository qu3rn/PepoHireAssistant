"""RocketJobs job collector.

⚠ **Status as of 2026-05**: All known public API endpoints at
``api.rocketjobs.pl/api/offers`` return HTTP 404 (Invalid endpoint).
No accessible public unauthenticated JSON API has been found for this source.

This collector returns 0 results with a clear diagnostic error.
Use the **Bookmarklet** to import individual rocketjobs.pl offers manually.
"""

from __future__ import annotations

import logging

from cv_sender.collectors.base import (
    BaseJobCollector,
    CollectedOffer,
    JobSearchCriteria,
)

logger = logging.getLogger(__name__)

# Last-known API base — kept for reference / future re-validation.
_API_BASE_DEAD = "https://api.rocketjobs.pl/api/offers"


class RocketJobsCollector(BaseJobCollector):
    """Collector for rocketjobs.pl.

    The public API endpoint is no longer available (HTTP 404).
    Returns 0 results with an explanatory diagnostic error.
    """

    source = "rocketjobs"

    def search(self, criteria: JobSearchCriteria) -> list[CollectedOffer]:
        logger.warning(
            "RocketJobs collector: public API endpoint (%s) is no longer available "
            "(HTTP 404). Use the Bookmarklet to import individual offers from "
            "rocketjobs.pl manually.",
            _API_BASE_DEAD,
        )
        return []

    def collect_and_filter(self, criteria):  # type: ignore[override]
        from cv_sender.collectors.base import JobCollectionResult  # noqa: PLC0415

        result = JobCollectionResult(source=self.source)
        result.errors.append(
            "RocketJobs public API endpoint is no longer available (HTTP 404). "
            "All tested endpoint variants return HTTP 404. "
            "Use the Bookmarklet to import offers from rocketjobs.pl manually."
        )
        return result
