"""Playwright-based collector for NoFluffJobs.com."""

from __future__ import annotations

import re
from urllib.parse import quote, urlparse

from cv_sender.collectors.base import JobSearchCriteria
from cv_sender.collectors.playwright_base import PlaywrightJobCollector, classify_collected_url

_JOB_URL_RE = re.compile(
    r"https?://(?:www\.)?nofluffjobs\.com/(?:[a-z]{2}/)?job/",
    re.IGNORECASE,
)


class PlaywrightNoFluffJobsCollector(PlaywrightJobCollector):
    """Opens NoFluffJobs.com listing pages and collects job-offer URLs."""

    source = "nofluffjobs"

    def build_search_urls(self, criteria: JobSearchCriteria) -> list[str]:
        urls: list[str] = []
        for kw in (criteria.keywords or [""])[:3]:
            if kw:
                slug = quote(kw.lower().replace(" ", "-"), safe="-")
                urls.append(f"https://nofluffjobs.com/{slug}")
            else:
                urls.append("https://nofluffjobs.com")
        seen: set[str] = set()
        unique: list[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                unique.append(u)
        return unique or ["https://nofluffjobs.com"]

    def is_job_url(self, url: str) -> bool:
        return classify_collected_url(self.source, url).type == "job_offer"

    def normalize_job_url(self, url: str) -> str:
        parsed = urlparse(url)
        # Normalize regional prefixes — always use the /job/ form
        path = re.sub(r"^/[a-z]{2}/job/", "/job/", parsed.path)
        return f"https://nofluffjobs.com{path}".rstrip("/")
