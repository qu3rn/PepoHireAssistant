"""Playwright-based collector for Pracuj.pl.

Pracuj.pl uses Cloudflare bot-protection that blocks headless browsers.
This collector is included for completeness and may work with headful mode
(headless=False) but is expected to frequently hit CAPTCHA / login walls.
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlparse

from cv_sender.collectors.base import JobSearchCriteria
from cv_sender.collectors.playwright_base import PlaywrightJobCollector

# Pracuj offer URLs have a slug followed by comma or semicolon and an offer ID/type
# e.g. https://www.pracuj.pl/praca/react-developer,oferta,1234567890
#      https://www.pracuj.pl/praca/frontend-dev-acme;1234567890
_JOB_URL_RE = re.compile(
    r"https?://(?:www\.)?pracuj\.pl/praca/[^/?#]+[,;][^/?#]+",
    re.IGNORECASE,
)


class PlaywrightPracujCollector(PlaywrightJobCollector):
    """Opens Pracuj.pl listing pages and collects job-offer URLs.

    Note: Pracuj.pl is protected by Cloudflare.  This collector may frequently
    be blocked and is best run with ``headless=False``.  The base class will
    detect the block and save a screenshot for diagnosis.
    """

    source = "pracuj"

    def build_search_urls(self, criteria: JobSearchCriteria) -> list[str]:
        urls: list[str] = []
        for kw in (criteria.keywords or [""])[:3]:
            if kw:
                # Pracuj uses /praca/<keyword>;kw search
                slug = quote(kw, safe="")
                urls.append(f"https://www.pracuj.pl/praca/{slug};kw")
            else:
                urls.append("https://www.pracuj.pl/praca")
        seen: set[str] = set()
        unique: list[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                unique.append(u)
        return unique or ["https://www.pracuj.pl/praca"]

    def is_job_url(self, url: str) -> bool:
        return bool(_JOB_URL_RE.match(url))

    def normalize_job_url(self, url: str) -> str:
        parsed = urlparse(url)
        # Strip query params and trailing slashes
        return f"https://www.pracuj.pl{parsed.path}".rstrip("/")
