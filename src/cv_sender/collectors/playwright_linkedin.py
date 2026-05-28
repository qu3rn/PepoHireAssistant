"""Playwright-based collector for public LinkedIn Jobs listing pages.

This collector is optional and should remain disabled by default. It only
collects URLs from public jobs pages and does not automate sign-in or attempt
to bypass login walls, CAPTCHAs, or bot protection.
"""

from __future__ import annotations

import re
from urllib.parse import quote_plus, urlparse

from cv_sender.collectors.base import JobSearchCriteria
from cv_sender.collectors.playwright_base import PlaywrightJobCollector

_JOB_URL_RE = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/jobs/view/\d+/?$",
    re.IGNORECASE,
)


class PlaywrightLinkedInCollector(PlaywrightJobCollector):
    """Collect public LinkedIn job URLs without automating authentication."""

    source = "linkedin"

    def build_search_urls(self, criteria: JobSearchCriteria) -> list[str]:
        urls: list[str] = []
        for kw in (criteria.keywords or [""])[:3]:
            if kw:
                urls.append(f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(kw)}")
            else:
                urls.append("https://www.linkedin.com/jobs/search/")
        seen: set[str] = set()
        unique: list[str] = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                unique.append(url)
        return unique or ["https://www.linkedin.com/jobs/search/"]

    def is_job_url(self, url: str) -> bool:
        return bool(_JOB_URL_RE.match(url))

    def normalize_job_url(self, url: str) -> str:
        parsed = urlparse(url)
        return f"https://www.linkedin.com{parsed.path}".rstrip("/")
