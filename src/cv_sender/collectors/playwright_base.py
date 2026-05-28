"""Base class and shared utilities for Playwright-based job-URL collectors.

These collectors open public job-listing pages in a real browser (Playwright),
scroll through results, collect offer URLs, and hand them to the existing
import/extraction/scoring pipeline.

IMPORTANT: These collectors do NOT bypass CAPTCHAs, login walls, or bot
protection.  They only browse publicly accessible listing pages.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None  # type: ignore[assignment]

from pydantic import BaseModel, Field

from cv_sender.collectors.base import JobSearchCriteria

if TYPE_CHECKING:
    pass  # playwright Page type used as Any to avoid hard dependency

logger = logging.getLogger(__name__)

_DEBUG_BASE = Path("data/debug/playwright_collectors")

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class PlaywrightCollectedUrl(BaseModel):
    """A single job-offer URL discovered on a listing page."""

    source: str
    url: str
    title_preview: str = ""
    company_preview: str = ""
    location_preview: str = ""
    salary_preview: str = ""
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    listing_url: str = ""
    raw_text_preview: str = ""


class PlaywrightCollectionResult(BaseModel):
    """Aggregated result from one Playwright-based collection run for a single source."""

    source: str
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    listing_urls: list[str] = Field(default_factory=list)
    collected_urls: list[PlaywrightCollectedUrl] = Field(default_factory=list)
    raw_link_count: int = 0
    normalized_link_count: int = 0
    duplicate_count: int = 0
    failed_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    debug_artifacts: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    @property
    def job_url_count(self) -> int:
        return len(self.collected_urls)


# ---------------------------------------------------------------------------
# Blocked-page detection
# ---------------------------------------------------------------------------

_CAPTCHA_SIGNALS = [
    "captcha",
    "i am not a robot",
    "i'm not a robot",
    "verify you are human",
    "just a moment",  # Cloudflare
    "checking your browser",
    "enable javascript and cookies",
    "ddos-guard",
    "bot protection",
]

_LOGIN_SIGNALS = [
    "sign in to",
    "log in to",
    "please login",
    "please sign in",
    "create an account",
    "password",
    "authentication required",
]

_BLOCKED_SIGNALS = [
    "access denied",
    "403 forbidden",
    "your ip has been blocked",
    "you have been blocked",
    "this page is not available",
    "geo-restriction",
]


def detect_captcha(page_text: str) -> bool:
    """Return True if page text suggests a CAPTCHA challenge."""
    lower = page_text.lower()
    return any(s in lower for s in _CAPTCHA_SIGNALS)


def detect_login_wall(page_text: str) -> bool:
    """Return True if page text suggests a login wall."""
    lower = page_text.lower()
    return any(s in lower for s in _LOGIN_SIGNALS)


def detect_blocked_page(page_text: str) -> bool:
    """Return True if page text suggests access is blocked."""
    lower = page_text.lower()
    return any(s in lower for s in _BLOCKED_SIGNALS)


def classify_page(page_text: str) -> str | None:
    """Return a classification string or None if the page looks normal."""
    if detect_captcha(page_text):
        return "captcha"
    if detect_blocked_page(page_text):
        return "blocked"
    if detect_login_wall(page_text):
        return "login_wall"
    return None


# ---------------------------------------------------------------------------
# Debug helpers
# ---------------------------------------------------------------------------


def _save_debug_artifacts(
    run_id: str,
    source: str,
    listing_url: str,
    links: list[str],
    page: Any | None,
    cfg: Any,
) -> list[str]:
    """Save debug screenshots / HTML / links to the debug directory."""
    artifacts: list[str] = []
    run_dir = _DEBUG_BASE / run_id / source
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Cannot create debug dir %s: %s", run_dir, exc)
        return artifacts

    # links.json
    links_path = run_dir / "links.json"
    try:
        links_path.write_text(
            json.dumps({"listing_url": listing_url, "links": links}, indent=2),
            encoding="utf-8",
        )
        artifacts.append(str(links_path))
    except OSError as exc:
        logger.warning("Cannot write links.json: %s", exc)

    # metadata.json
    meta_path = run_dir / "metadata.json"
    try:
        meta_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "source": source,
                    "listing_url": listing_url,
                    "raw_link_count": len(links),
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        artifacts.append(str(meta_path))
    except OSError as exc:
        logger.warning("Cannot write metadata.json: %s", exc)

    if page is None:
        return artifacts

    # Screenshot
    if getattr(cfg, "save_debug_screenshots", True):
        screenshot_path = run_dir / "screenshot.png"
        try:
            page.screenshot(path=str(screenshot_path), full_page=False)
            artifacts.append(str(screenshot_path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Screenshot failed: %s", exc)

    # HTML preview (size-limited to 500 KB)
    if getattr(cfg, "save_debug_html_preview", False):
        html_path = run_dir / "html_preview.html"
        try:
            html = page.content()
            html_path.write_text(html[:500_000], encoding="utf-8")
            artifacts.append(str(html_path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("HTML preview failed: %s", exc)

    return artifacts


# ---------------------------------------------------------------------------
# Base collector
# ---------------------------------------------------------------------------


class PlaywrightJobCollector(ABC):
    """Base class for Playwright-based job-URL collectors.

    Subclasses must implement:
    - :meth:`build_search_urls` — list of listing URLs to open
    - :meth:`extract_links_from_page` — pull raw hrefs from a Playwright page
    - :meth:`is_job_url` — filter that accepts only real offer URLs
    - :meth:`normalize_job_url` — canonicalize a URL

    The base class handles:
    - Browser launch / context management
    - Gradual scrolling
    - Blocked-page detection
    - Deduplication
    - Debug artifact saving
    """

    source: str = ""

    # Subclasses override to tune link extraction
    _link_selector: str = "a[href]"

    @abstractmethod
    def build_search_urls(self, criteria: JobSearchCriteria) -> list[str]:
        """Return a list of search/listing page URLs for this source."""

    @abstractmethod
    def is_job_url(self, url: str) -> bool:
        """Return True if *url* looks like a genuine job offer URL for this source."""

    def normalize_job_url(self, url: str) -> str:
        """Return the canonical URL for a job offer (strip query/fragment by default)."""
        from urllib.parse import urlparse, urlunparse  # noqa: PLC0415

        parsed = urlparse(url)
        # Strip query params and fragment; keep scheme + netloc + path
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))

    def extract_links_from_page(self, page: Any, base_url: str) -> list[str]:
        """Extract all href values from the page and resolve relative URLs."""
        from urllib.parse import urljoin, urlparse  # noqa: PLC0415

        try:
            hrefs: list[str] = page.eval_on_selector_all(
                self._link_selector,
                "els => els.map(el => el.href || el.getAttribute('href') || '')",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Link extraction failed on %s: %s", base_url, exc)
            return []

        result: list[str] = []
        parsed_base = urlparse(base_url)
        base = f"{parsed_base.scheme}://{parsed_base.netloc}"

        for href in hrefs:
            if not href or href.startswith(("javascript:", "mailto:", "#")):
                continue
            if href.startswith("//"):
                href = parsed_base.scheme + ":" + href
            elif href.startswith("/"):
                href = base + href
            elif not href.startswith("http"):
                href = urljoin(base_url, href)
            result.append(href)

        return result

    def collect_urls(
        self,
        criteria: JobSearchCriteria,
        cfg: Any,
        custom_listing_urls: list[str] | None = None,
    ) -> PlaywrightCollectionResult:
        """Run the full Playwright collection flow for this source.

        Returns a :class:`PlaywrightCollectionResult` without importing anything.
        """
        result = PlaywrightCollectionResult(source=self.source)

        listing_urls = custom_listing_urls or self.build_search_urls(criteria)
        result.listing_urls = listing_urls

        if not listing_urls:
            result.errors.append(f"No listing URLs built for source {self.source!r}")
            return result

        if sync_playwright is None:
            result.errors.append(
                "Playwright is not installed. Run: pip install playwright && playwright install chromium"
            )
            return result

        max_urls = getattr(cfg, "max_urls_per_source", 50)
        headless = getattr(cfg, "headless", False)
        slow_mo = getattr(cfg, "slow_mo_ms", 150)
        page_timeout = getattr(cfg, "page_timeout_ms", 30_000)
        max_scrolls = getattr(cfg, "max_scrolls_per_source", 8)
        scroll_pause = getattr(cfg, "scroll_pause_ms", 1200) / 1000.0
        user_agent = getattr(cfg, "user_agent", None)

        seen_urls: set[str] = set()
        all_raw_links: list[str] = []
        all_extracted_links: list[str] = []  # all hrefs before source-specific filtering

        with sync_playwright() as pw:
            launch_kwargs: dict = {"headless": headless, "slow_mo": slow_mo}
            browser = pw.chromium.launch(**launch_kwargs)
            context_kwargs: dict = {}
            if user_agent:
                context_kwargs["user_agent"] = user_agent
            context = browser.new_context(**context_kwargs)
            context.set_default_timeout(page_timeout)

            try:
                for listing_url in listing_urls:
                    if len(result.collected_urls) >= max_urls:
                        break

                    page = context.new_page()
                    try:
                        all_page_hrefs, page_links, artifacts, warning = self._process_listing_page(
                            page=page,
                            listing_url=listing_url,
                            max_scrolls=max_scrolls,
                            scroll_pause=scroll_pause,
                            max_urls=max_urls,
                            seen_urls=seen_urls,
                            result=result,
                            cfg=cfg,
                        )
                        all_raw_links.extend(all_page_hrefs)
                        all_extracted_links.extend(all_page_hrefs)
                        result.debug_artifacts.extend(artifacts)
                        if warning:
                            result.warnings.append(warning)
                    except Exception as exc:  # noqa: BLE001
                        msg = f"{self.source} listing {listing_url}: {exc}"
                        result.errors.append(msg)
                        logger.error("Playwright collection error: %s", msg)
                        _save_debug_artifacts(result.run_id, self.source, listing_url, [], page, cfg)
                    finally:
                        try:
                            page.close()
                        except Exception:  # noqa: BLE001
                            pass
            finally:
                try:
                    context.close()
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass

        result.raw_link_count = len(all_extracted_links)
        result.finished_at = datetime.now(UTC)

        if all_extracted_links and not result.collected_urls:
            sample = [l for l in all_raw_links[:5]]
            result.warnings.append(
                f"Found {result.raw_link_count} links on {self.source} but 0 matched job URL patterns. "
                f"Sample rejected links: {sample}"
            )

        return result

    def _process_listing_page(
        self,
        page: Any,
        listing_url: str,
        max_scrolls: int,
        scroll_pause: float,
        max_urls: int,
        seen_urls: set[str],
        result: PlaywrightCollectionResult,
        cfg: Any,
    ) -> tuple[list[str], list[str], list[str], str]:
        """Navigate to *listing_url*, scroll, extract links, and populate *result*.

        Returns (all_hrefs_on_page, normalized_job_urls, debug_artifact_paths, warning_string).
        """
        logger.info("Playwright: opening %s → %s", self.source, listing_url)

        page.goto(listing_url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=10_000)

        # Check for blocked/CAPTCHA/login pages
        page_text = ""
        try:
            page_text = page.inner_text("body")
        except Exception:  # noqa: BLE001
            pass

        classification = classify_page(page_text)
        if classification:
            warning = f"{self.source}: page at {listing_url} is {classification}. Cannot collect."
            logger.warning(warning)
            artifacts = _save_debug_artifacts(result.run_id, self.source, listing_url, [], page, cfg)
            return [], [], artifacts, warning

        all_hrefs: list[str] = []
        all_page_links: list[str] = []
        no_new_consecutive = 0

        for scroll_n in range(max_scrolls + 1):
            links = self.extract_links_from_page(page, listing_url)
            all_hrefs.extend(links)
            new_count = 0

            for href in links:
                if not self.is_job_url(href):
                    continue
                norm = self.normalize_job_url(href)
                if norm in seen_urls:
                    result.duplicate_count += 1
                    continue
                seen_urls.add(norm)
                all_page_links.append(norm)
                new_count += 1
                result.normalized_link_count += 1
                result.collected_urls.append(
                    PlaywrightCollectedUrl(
                        source=self.source,
                        url=norm,
                        listing_url=listing_url,
                    )
                )
                if len(result.collected_urls) >= max_urls:
                    break

            if len(result.collected_urls) >= max_urls:
                break

            if new_count == 0:
                no_new_consecutive += 1
                if no_new_consecutive >= 2:
                    logger.debug("%s: no new links after %d scrolls, stopping", self.source, scroll_n)
                    break
            else:
                no_new_consecutive = 0

            if scroll_n < max_scrolls:
                page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
                time.sleep(scroll_pause)

        artifacts = _save_debug_artifacts(result.run_id, self.source, listing_url, all_hrefs, page, cfg)
        return all_hrefs, all_page_links, artifacts, ""
