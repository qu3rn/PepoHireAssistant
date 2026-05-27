"""LinkedIn collector stub — LinkedIn requires login; manual import recommended."""

from __future__ import annotations

import logging

from cv_sender.collectors.base import (
    BaseJobCollector,
    CollectedOffer,
    JobSearchCriteria,
)

logger = logging.getLogger(__name__)


class LinkedInCollector(BaseJobCollector):
    """Stub collector for LinkedIn.

    LinkedIn's job search requires authentication or aggressive JS rendering.
    This stub always returns an empty list with an advisory warning.
    Use the Bookmarklet feature or manual URL import for LinkedIn offers.
    """

    source = "linkedin"

    def search(self, criteria: JobSearchCriteria) -> list[CollectedOffer]:  # noqa: ARG002
        logger.warning(
            "LinkedIn collector is a stub. "
            "Use the Bookmarklet or manual URL import for LinkedIn offers."
        )
        return []
