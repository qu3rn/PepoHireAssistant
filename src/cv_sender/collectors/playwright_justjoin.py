"""Playwright-based collector for JustJoin.it."""

from __future__ import annotations

import re
from urllib.parse import quote_plus, urlparse

from cv_sender.collectors.base import JobSearchCriteria
from cv_sender.collectors.playwright_base import PlaywrightJobCollector, classify_collected_url

_JOB_URL_RE = re.compile(
    r"https?://(?:www\.)?justjoin\.it/(?:offers?|job-offers?|listing)/",
    re.IGNORECASE,
)


class PlaywrightJustJoinCollector(PlaywrightJobCollector):
    """Opens JustJoin.it listing pages and collects job-offer URLs.

    JustJoin.it uses a React SPA, so the URLs themselves contain the filter
    state.  We open the keyword-filtered listing and scroll to load more offers.
    """

    source = "justjoin"

    def build_search_urls(self, criteria: JobSearchCriteria) -> list[str]:
        urls: list[str] = []
        # Combine all keywords into individual searches for breadth
        for kw in (criteria.keywords or [""])[:3]:
            encoded_kw = quote_plus(kw) if kw else ""
            if encoded_kw:
                urls.append(f"https://justjoin.it/job-offers?keyword={encoded_kw}")
            else:
                urls.append("https://justjoin.it/job-offers")
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                unique.append(u)
        return unique or ["https://justjoin.it/job-offers"]

    def is_job_url(self, url: str) -> bool:
        return classify_collected_url(self.source, url).type == "job_offer"

    def normalize_job_url(self, url: str) -> str:
        parsed = urlparse(url)
        # Keep path; strip query params (they contain analytics noise)
        return f"https://justjoin.it{parsed.path}"
