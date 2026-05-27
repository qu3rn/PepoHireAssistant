"""Pracuj.pl job collector.

⚠ **Status as of 2026-05**: Pracuj.pl is protected by Cloudflare bot-protection.
All unauthenticated HTTP requests return HTTP 403 with a Cloudflare challenge page
("Just a moment…").  No accessible public JSON API is available.

This collector returns 0 results with a clear diagnostic error.
Use the **Bookmarklet** to import individual pracuj.pl offers manually.
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
_API_BASE_DEAD = "https://massachusetts.pracuj.pl/jobs"


class PracujCollector(BaseJobCollector):
    """Collector for pracuj.pl.

    Pracuj.pl blocks all unauthenticated requests via Cloudflare (HTTP 403).
    Returns 0 results with an explanatory diagnostic error.
    """

    source = "pracuj"

    def search(self, criteria: JobSearchCriteria) -> list[CollectedOffer]:
        logger.warning(
            "Pracuj collector: pracuj.pl is protected by Cloudflare bot-protection "
            "(HTTP 403 on %s). "
            "Use the Bookmarklet to import individual offers from pracuj.pl manually.",
            _API_BASE_DEAD,
        )
        return []

    def collect_and_filter(self, criteria):  # type: ignore[override]
        from cv_sender.collectors.base import JobCollectionResult  # noqa: PLC0415

        result = JobCollectionResult(source=self.source)
        result.errors.append(
            "Pracuj.pl is blocked by Cloudflare bot-protection (HTTP 403). "
            "All unauthenticated API and scraping attempts are blocked. "
            "Use the Bookmarklet to import offers from pracuj.pl manually."
        )
        return result
