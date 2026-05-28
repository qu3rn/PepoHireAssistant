"""Playwright-based collector for RocketJobs.pl."""

from __future__ import annotations

import re
from urllib.parse import quote, urlparse

from cv_sender.collectors.base import JobSearchCriteria
from cv_sender.collectors.playwright_base import PlaywrightJobCollector, classify_collected_url

_JOB_URL_RE = re.compile(
    r"https?://(?:www\.)?rocketjobs\.pl/oferty-pracy/[^/]+/?$",
    re.IGNORECASE,
)


class PlaywrightRocketJobsCollector(PlaywrightJobCollector):
    """Opens RocketJobs.pl listing pages and collects job-offer URLs."""

    source = "rocketjobs"

    def build_search_urls(self, criteria: JobSearchCriteria) -> list[str]:
        urls: list[str] = []
        for kw in (criteria.keywords or [""])[:3]:
            if kw:
                slug = quote(kw.lower().replace(" ", "-"), safe="-")
                urls.append(f"https://rocketjobs.pl/oferty-pracy?q={slug}")
            else:
                urls.append("https://rocketjobs.pl/oferty-pracy")
        seen: set[str] = set()
        unique: list[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                unique.append(u)
        return unique or ["https://rocketjobs.pl/oferty-pracy"]

    def is_job_url(self, url: str) -> bool:
        return classify_collected_url(self.source, url).type == "job_offer"

    def normalize_job_url(self, url: str) -> str:
        parsed = urlparse(url)
        return f"https://rocketjobs.pl{parsed.path}".rstrip("/")
