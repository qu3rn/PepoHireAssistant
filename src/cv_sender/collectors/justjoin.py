"""JustJoinIT job collector.

⚠ **Status as of 2026-05**: The public unauthenticated API endpoints at
``api.justjoin.it/v2/user-panel/offers`` are no longer available (HTTP 404).
The authenticated endpoint (``/v2/user-panel/offers/active``) requires login (HTTP 401).
The old public API (``justjoin.it/api/offers``) also returns 404.

No accessible public unauthenticated JSON API has been found for this source.
This collector returns 0 results with a clear diagnostic error.

Use the **Bookmarklet** to import individual justjoin.it offers manually.
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
_API_BASE_DEAD = "https://api.justjoin.it/v2/user-panel/offers"

# Diagnostic probe: to confirm the endpoint is still down, run:
#   GET https://api.justjoin.it/v2/user-panel/offers?page=1&perPage=5
# Expected: HTTP 404  {"statusCode":404,"message":"Cannot GET /v2/user-panel/offers..."}


class JustJoinCollector(BaseJobCollector):
    """Collector for justjoin.it.

    The public API was removed; this collector always returns 0 with an
    explanatory error so the diagnostics page shows a clear reason.
    """

    source = "justjoin"

    def search(self, criteria: JobSearchCriteria) -> list[CollectedOffer]:
        logger.warning(
            "JustJoin collector: public API endpoint (%s) is no longer available "
            "(HTTP 404). Use the Bookmarklet to import individual offers from "
            "justjoin.it manually.",
            _API_BASE_DEAD,
        )
        return []

    def collect_and_filter(self, criteria):  # type: ignore[override]
        from cv_sender.collectors.base import JobCollectionResult  # noqa: PLC0415

        result = JobCollectionResult(source=self.source)
        result.errors.append(
            "JustJoin public API endpoint is no longer available (HTTP 404). "
            "The authenticated user-panel endpoint requires login (HTTP 401). "
            "No accessible public API found. "
            "Use the Bookmarklet to import offers from justjoin.it manually."
        )
        return result
