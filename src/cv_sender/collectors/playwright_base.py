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
from typing import TYPE_CHECKING, Any, Literal
try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None  # type: ignore[assignment]

from pydantic import BaseModel, Field

from cv_sender.collectors.base import JobSearchCriteria
from cv_sender.playwright_helpers import detect_cookie_banner_visible, detect_login_detection, handle_common_modals
from cv_sender.relevance import EmergencyReactMode, match_offer_relevance

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
    classification_type: str = "unknown"
    classification_reason: str = "unknown"
    relevance_score: int = 0
    relevance_decision: str = "irrelevant"
    matched_keywords: list[str] = Field(default_factory=list)
    matched_technologies: list[str] = Field(default_factory=list)
    matched_languages: list[str] = Field(default_factory=list)
    rejected_keywords: list[str] = Field(default_factory=list)
    relevance_reasons: list[str] = Field(default_factory=list)
    relevance_warnings: list[str] = Field(default_factory=list)
    suggested_action: str = "ignore"


CollectedUrlType = Literal["job_offer", "listing", "company", "navigation", "unknown", "needs_review"]


class UrlClassification(BaseModel):
    type: CollectedUrlType
    reason: str
    source: str
    url: str


class PlaywrightCollectionResult(BaseModel):
    """Aggregated result from one Playwright-based collection run for a single source."""

    source: str
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    listing_urls: list[str] = Field(default_factory=list)
    collected_urls: list[PlaywrightCollectedUrl] = Field(default_factory=list)
    collected_listing_urls: list[PlaywrightCollectedUrl] = Field(default_factory=list)
    company_urls: list[PlaywrightCollectedUrl] = Field(default_factory=list)
    navigation_urls: list[PlaywrightCollectedUrl] = Field(default_factory=list)
    unknown_urls: list[PlaywrightCollectedUrl] = Field(default_factory=list)
    needs_review_urls: list[PlaywrightCollectedUrl] = Field(default_factory=list)
    rejected_examples: dict[str, list[str]] = Field(default_factory=dict)
    raw_link_count: int = 0
    normalized_link_count: int = 0
    duplicate_count: int = 0
    failed_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    modal_actions: list[str] = Field(default_factory=list)
    modal_warnings: list[str] = Field(default_factory=list)
    modal_handler_called: bool = False
    cookie_banner_visible_before: bool = False
    cookie_banner_visible_after: bool = False
    blocked_by_captcha: bool = False
    blocked_by_login: bool = False
    blocked_by_overlay: bool = False
    errors: list[str] = Field(default_factory=list)
    debug_artifacts: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    @property
    def job_url_count(self) -> int:
        return len(self.collected_urls)


def _canonicalize_url(url: str) -> str:
    from urllib.parse import urlparse, urlunparse  # noqa: PLC0415

    parsed = urlparse(url)
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))


def _path_segments(path: str) -> list[str]:
    return [seg for seg in path.split("/") if seg]


def classify_collected_url(source: str, url: str) -> UrlClassification:
    """Classify URL shape by source, independent from job relevance."""
    from urllib.parse import urlparse  # noqa: PLC0415

    parsed = urlparse(url)
    host = parsed.netloc.lower().replace("www.", "")
    path = parsed.path.lower()
    if parsed.params:
        path = f"{path};{parsed.params.lower()}"
    segments = _path_segments(path)
    src = source.lower()

    def out(c_type: CollectedUrlType, reason: str) -> UrlClassification:
        return UrlClassification(type=c_type, reason=reason, source=src, url=url)

    # Global navigation/login/account patterns.
    if any(token in path for token in ("/login", "/logowanie", "/signin", "/rejestracja", "/register", "/konto", "/account", "/profile", "/profil")):
        return out("navigation", "global_auth_or_profile_path")
    if any(token in path for token in ("/blog", "/faq", "/help", "/about", "/kontakt", "/contact", "/kalkulator")):
        return out("navigation", "global_navigation_path")

    # Company-like pages.
    if any(token in path for token in ("/company", "/companies", "/firmy", "/brands")):
        return out("company", "global_company_path")

    if src == "pracuj" or "pracuj.pl" in host:
        if ",oferta," in path:
            return out("job_offer", "pracuj_offer_id_pattern")
        if re.search(r";\d{8,}", path):
            return out("job_offer", "pracuj_offer_semicolon_id_pattern")
        if path == "/praca" or path.startswith("/praca/"):
            return out("listing", "pracuj_listing_praca_path")
        return out("unknown", "pracuj_unknown_path")

    if src == "rocketjobs" or "rocketjobs.pl" in host:
        if path.startswith("/firmy") or path.startswith("/firma"):
            return out("company", "rocketjobs_company_path")
        if path == "/oferta-pracy":
            return out("needs_review", "rocketjobs_offer_path_missing_slug")
        if path.startswith("/oferta-pracy/"):
            slug = path.removeprefix("/oferta-pracy/").strip("/")
            if slug:
                return out("job_offer", "rocketjobs_oferta_pracy_path")
            return out("needs_review", "rocketjobs_offer_path_missing_slug")
        if path == "/oferty-pracy" or path.startswith("/oferty-pracy/"):
            return out("listing", "rocketjobs_listing_oferty_pracy_path")
        if path == "/praca" or path.startswith("/praca"):
            return out("listing", "rocketjobs_listing_praca_path")
        if path.startswith("/oferta"):
            return out("needs_review", "rocketjobs_offer_like_unknown_path")
        return out("unknown", "rocketjobs_unknown_path")

    if src == "justjoin" or "justjoin.it" in host:
        if path.startswith("/companies") or path.startswith("/company") or path.startswith("/brands"):
            return out("company", "justjoin_company_path")
        if path == "/job-offer":
            return out("needs_review", "justjoin_offer_path_missing_slug")
        if path.startswith("/job-offer/"):
            slug = path.removeprefix("/job-offer/").strip("/")
            if slug:
                return out("job_offer", "justjoin_job_offer_path")
            return out("needs_review", "justjoin_offer_path_missing_slug")
        if path == "/job-offers" or path.startswith("/job-offers/"):
            return out("listing", "justjoin_listing_job_offers_path")
        return out("unknown", "justjoin_unknown_path")

    if src == "nofluffjobs" or "nofluffjobs.com" in host:
        if path.startswith("/company") or path.startswith("/companies"):
            return out("company", "nofluffjobs_company_path")
        if path in ("", "/", "/pl"):
            return out("listing", "nofluffjobs_home_listing_path")
        if path.startswith("/job/") or path.startswith("/pl/job/"):
            slug = segments[-1] if segments else ""
            if slug and slug not in ("job",):
                return out("job_offer", "nofluffjobs_job_path")
            return out("needs_review", "nofluffjobs_job_path_missing_slug")
        if path.startswith("/pl/") or len(segments) == 1:
            return out("listing", "nofluffjobs_listing_path")
        return out("unknown", "nofluffjobs_unknown_path")

    if src == "linkedin" or "linkedin.com" in host:
        if "/jobs/view/" in path and re.search(r"/jobs/view/\d+", path):
            return out("job_offer", "linkedin_jobs_view_path")
        if "/jobs/search" in path:
            return out("listing", "linkedin_jobs_search_path")
        return out("unknown", "linkedin_unknown_path")

    return out("unknown", "unknown_source_or_path")


def _append_rejected_example(result: PlaywrightCollectionResult, bucket: str, url: str, max_examples: int = 8) -> None:
    examples = result.rejected_examples.setdefault(bucket, [])
    if len(examples) < max_examples and url not in examples:
        examples.append(url)


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


def _empty_login_detection(current_url: str = "") -> dict[str, Any]:
    return {
        "navigation_login_link_detected": False,
        "login_wall_detected": False,
        "login_redirect_detected": False,
        "login_form_detected": False,
        "reason": "",
        "detected_texts": [],
        "current_url": current_url,
        "useful_content_detected": False,
    }


# ---------------------------------------------------------------------------
# Debug helpers
# ---------------------------------------------------------------------------


def _save_debug_artifacts(
    run_id: str,
    source: str,
    listing_url: str,
    links: list[str] | None,
    page: Any | None,
    cfg: Any,
    *,
    metadata: dict[str, Any] | None = None,
    modal_actions: list[dict[str, Any]] | None = None,
    screenshot_name: str = "screenshot_after_scroll.png",
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
    if links is not None:
        links_path = run_dir / "links.json"
        try:
            links_path.write_text(
                json.dumps({"listing_url": listing_url, "links": links}, indent=2, ensure_ascii=False, default=str),
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
                metadata
                or {
                    "run_id": run_id,
                    "source": source,
                    "listing_url": listing_url,
                    "raw_link_count": len(links or []),
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )
        artifacts.append(str(meta_path))
    except OSError as exc:
        logger.warning("Cannot write metadata.json: %s", exc)

    if modal_actions is not None:
        modal_path = run_dir / "modal_actions.json"
        try:
            modal_path.write_text(
                json.dumps(modal_actions, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            artifacts.append(str(modal_path))
        except OSError as exc:
            logger.warning("Cannot write modal_actions.json: %s", exc)

    if page is None:
        return artifacts

    # Screenshot
    if getattr(cfg, "save_debug_screenshots", True):
        screenshot_path = run_dir / screenshot_name
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


def _summarize_modal_result(modal_result: Any) -> dict[str, Any]:
    actions = list(getattr(modal_result, "actions_taken", []))
    return {
        "handler_called": bool(getattr(modal_result, "handler_called", False)),
        "cookie_banner_visible_before": bool(getattr(modal_result, "cookie_banner_visible_before", False)),
        "cookie_banner_visible_after": bool(getattr(modal_result, "cookie_banner_visible_after", False)),
        "actions_count": len(actions),
        "actions_success_count": sum(1 for action in actions if getattr(action, "status", "") == "success"),
        "warnings": list(getattr(modal_result, "warnings", [])),
    }


def _modal_screenshot_enabled(settings: Any) -> bool:
    if settings is None:
        return True
    if hasattr(settings, "playwright") and hasattr(settings.playwright, "modals"):
        return bool(getattr(settings.playwright.modals, "screenshot_after_handling", True))
    if hasattr(settings, "modals"):
        return bool(getattr(settings.modals, "screenshot_after_handling", True))
    if isinstance(settings, dict):
        return bool(settings.get("screenshot_after_handling", True))
    return True


def _merge_modal_result(result: PlaywrightCollectionResult, source: str, modal_result: Any) -> None:
    result.modal_handler_called = result.modal_handler_called or bool(getattr(modal_result, "handler_called", False))
    result.cookie_banner_visible_before = result.cookie_banner_visible_before or bool(
        getattr(modal_result, "cookie_banner_visible_before", False)
    )
    result.cookie_banner_visible_after = bool(getattr(modal_result, "cookie_banner_visible_after", False))
    result.blocked_by_captcha = result.blocked_by_captcha or bool(getattr(modal_result, "blocked_by_captcha", False))
    result.blocked_by_login = result.blocked_by_login or bool(getattr(modal_result, "blocked_by_login", False))
    result.blocked_by_overlay = result.blocked_by_overlay or bool(getattr(modal_result, "blocked_by_overlay", False))
    for action in getattr(modal_result, "actions_taken", []):
        result.modal_actions.append(f"{source}:{action.type}:{action.status}:{action.selector_or_text}")
    for warning in getattr(modal_result, "warnings", []):
        result.modal_warnings.append(warning)
        result.warnings.append(f"{source}: {warning}")


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

        emergency_mode = EmergencyReactMode()
        modal_settings: Any = None
        try:
            from cv_sender.config import load_settings  # noqa: PLC0415

            settings = load_settings()
            em_cfg = settings.job_search.emergency_react_mode
            modal_settings = settings
            emergency_mode = EmergencyReactMode(
                enabled=bool(em_cfg.enabled),
                accept_needs_review=bool(em_cfg.accept_needs_review),
                reject_obvious_non_it=bool(em_cfg.reject_obvious_non_it),
                min_relevance_score=int(em_cfg.min_relevance_score),
                needs_review_score=int(em_cfg.needs_review_score),
            )
        except Exception:  # noqa: BLE001
            pass

        seen_urls: set[str] = set()
        seen_by_type: dict[str, set[str]] = {
            "listing": set(),
            "company": set(),
            "navigation": set(),
            "unknown": set(),
            "needs_review": set(),
        }
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
                            seen_by_type=seen_by_type,
                            result=result,
                            cfg=cfg,
                            criteria=criteria,
                            emergency_mode=emergency_mode,
                            modal_settings=modal_settings,
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

        if result.needs_review_urls:
            result.warnings.append(
                f"{self.source}: {len(result.needs_review_urls)} URL(s) need review and were not imported automatically."
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
        seen_by_type: dict[str, set[str]],
        result: PlaywrightCollectionResult,
        cfg: Any,
        criteria: JobSearchCriteria,
        emergency_mode: EmergencyReactMode,
        modal_settings: Any,
    ) -> tuple[list[str], list[str], list[str], str]:
        """Navigate to *listing_url*, scroll, extract links, and populate *result*.

        Returns (all_hrefs_on_page, normalized_job_urls, debug_artifact_paths, warning_string).
        """
        logger.info("Playwright: opening %s → %s", self.source, listing_url)

        page.goto(listing_url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:  # noqa: BLE001
            pass
        try:
            page.wait_for_timeout(400)
        except Exception:  # noqa: BLE001
            pass

        if getattr(cfg, "save_debug_screenshots", True):
            _save_debug_artifacts(
                result.run_id,
                self.source,
                listing_url,
                None,
                page,
                cfg,
                screenshot_name="screenshot_initial.png",
            )

        modal_runs: list[Any] = []
        modal_result = handle_common_modals(page, modal_settings, context="collection")
        modal_runs.append(modal_result)
        _merge_modal_result(result, self.source, modal_result)

        if _modal_screenshot_enabled(modal_settings) and getattr(cfg, "save_debug_screenshots", True):
            try:
                page.wait_for_timeout(350)
            except Exception:  # noqa: BLE001
                pass
            _save_debug_artifacts(
                result.run_id,
                self.source,
                listing_url,
                None,
                page,
                cfg,
                screenshot_name="screenshot_after_modals.png",
            )

        login_detection_payload = getattr(modal_result, "login_detection", None)
        if not isinstance(login_detection_payload, dict):
            login_detection_payload = _empty_login_detection(getattr(page, "url", "") or "")

        if modal_result.blocked_by_captcha:
            warning = f"{self.source}: captcha detected at {listing_url}. Cannot collect."
            artifacts = _save_debug_artifacts(
                result.run_id,
                self.source,
                listing_url,
                [],
                page,
                cfg,
                metadata={
                    "run_id": result.run_id,
                    "source": self.source,
                    "keyword": criteria.keywords[0] if criteria.keywords else "",
                    "query": ", ".join(criteria.keywords),
                    "listing_url": listing_url,
                    "final_url": getattr(page, "url", "") or "",
                    "page_title": "",
                    "status": "blocked",
                    "raw_link_count": 0,
                    "job_offer_count": 0,
                    "listing_count": 0,
                    "needs_review_count": 0,
                    "unknown_count": 0,
                    "started_at": result.started_at,
                    "finished_at": datetime.now(UTC),
                    "modal_summary": _summarize_modal_result(modal_result),
                    "warnings": list(dict.fromkeys(result.modal_warnings + [warning])),
                    "detected_captcha": True,
                    "detected_login_wall": False,
                    "detected_blocked_page": False,
                    "login_detection": login_detection_payload,
                },
                modal_actions=[action.model_dump(mode="json") for action in modal_result.actions_taken],
            )
            return [], [], artifacts, warning
        if modal_result.blocked_by_login:
            warning = f"{self.source}: login wall detected at {listing_url}. Cannot collect."
            artifacts = _save_debug_artifacts(
                result.run_id,
                self.source,
                listing_url,
                [],
                page,
                cfg,
                metadata={
                    "run_id": result.run_id,
                    "source": self.source,
                    "keyword": criteria.keywords[0] if criteria.keywords else "",
                    "query": ", ".join(criteria.keywords),
                    "listing_url": listing_url,
                    "final_url": getattr(page, "url", "") or "",
                    "page_title": "",
                    "status": "blocked",
                    "raw_link_count": 0,
                    "job_offer_count": 0,
                    "listing_count": 0,
                    "needs_review_count": 0,
                    "unknown_count": 0,
                    "started_at": result.started_at,
                    "finished_at": datetime.now(UTC),
                    "modal_summary": _summarize_modal_result(modal_result),
                    "warnings": list(dict.fromkeys(result.modal_warnings + [warning])),
                    "detected_captcha": False,
                    "detected_login_wall": True,
                    "detected_blocked_page": False,
                    "login_detection": login_detection_payload,
                },
                modal_actions=[action.model_dump(mode="json") for action in modal_result.actions_taken],
            )
            return [], [], artifacts, warning

        # Check for blocked/CAPTCHA/login pages
        page_text = ""
        try:
            page_text = page.inner_text("body")
        except Exception:  # noqa: BLE001
            pass

        login_detection = detect_login_detection(page, original_listing_url=listing_url)
        login_detection_payload = login_detection.model_dump(mode="json")

        captcha_detected = detect_captcha(page_text)
        blocked_detected = detect_blocked_page(page_text)
        login_wall_detected = bool(login_detection.login_wall_detected)

        if login_detection.navigation_login_link_detected and not login_wall_detected:
            result.warnings.append(
                f"{self.source}: Login link detected in navigation, but page content is accessible."
            )

        classification: str | None = None
        if captcha_detected:
            classification = "captcha"
        elif blocked_detected:
            classification = "blocked"
        elif login_wall_detected:
            classification = "login_wall"

        if classification:
            reason = login_detection.reason if classification == "login_wall" else classification
            warning = f"{self.source}: page at {listing_url} is {classification}. Cannot collect. ({reason})"
            logger.warning(warning)
            artifacts = _save_debug_artifacts(
                result.run_id,
                self.source,
                listing_url,
                [],
                page,
                cfg,
                metadata={
                    "run_id": result.run_id,
                    "source": self.source,
                    "keyword": criteria.keywords[0] if criteria.keywords else "",
                    "query": ", ".join(criteria.keywords),
                    "listing_url": listing_url,
                    "final_url": getattr(page, "url", "") or "",
                    "page_title": "",
                    "status": "blocked",
                    "raw_link_count": 0,
                    "job_offer_count": 0,
                    "listing_count": 0,
                    "needs_review_count": 0,
                    "unknown_count": 0,
                    "started_at": result.started_at,
                    "finished_at": datetime.now(UTC),
                    "modal_summary": _summarize_modal_result(modal_result),
                    "warnings": list(dict.fromkeys(result.modal_warnings + [warning])),
                    "detected_captcha": classification == "captcha",
                    "detected_login_wall": classification == "login_wall",
                    "detected_blocked_page": classification == "blocked",
                    "login_detection": login_detection_payload,
                },
                modal_actions=[action.model_dump(mode="json") for action in modal_result.actions_taken],
            )
            return [], [], artifacts, warning

        all_hrefs: list[str] = []
        all_page_links: list[str] = []
        no_new_consecutive = 0

        for scroll_n in range(max_scrolls + 1):
            links = self.extract_links_from_page(page, listing_url)
            all_hrefs.extend(links)
            new_count = 0

            for href in links:
                classification = classify_collected_url(self.source, href)

                if classification.type == "job_offer":
                    norm = self.normalize_job_url(href)
                    if norm in seen_urls:
                        result.duplicate_count += 1
                        continue

                    relevance = match_offer_relevance(
                        {
                            "source": self.source,
                            "url": norm,
                            "title_preview": "",
                            "raw_text_preview": "",
                        },
                        criteria,
                        emergency_mode=emergency_mode,
                    )

                    seen_urls.add(norm)
                    all_page_links.append(norm)
                    new_count += 1
                    result.normalized_link_count += 1
                    suggested_action = "import"
                    if relevance.decision == "irrelevant":
                        suggested_action = "ignore"
                    elif relevance.decision == "needs_review":
                        suggested_action = "import anyway" if emergency_mode.accept_needs_review else "ignore"

                    result.collected_urls.append(
                        PlaywrightCollectedUrl(
                            source=self.source,
                            url=norm,
                            listing_url=listing_url,
                            classification_type=classification.type,
                            classification_reason=classification.reason,
                            relevance_score=relevance.score,
                            relevance_decision=relevance.decision,
                            matched_keywords=relevance.matched_keywords,
                            matched_technologies=relevance.matched_technologies,
                            matched_languages=relevance.matched_languages,
                            rejected_keywords=relevance.rejected_keywords,
                            relevance_reasons=relevance.reasons,
                            relevance_warnings=relevance.warnings,
                            suggested_action=suggested_action,
                        )
                    )
                    if len(result.collected_urls) >= max_urls:
                        break
                    continue

                canon = _canonicalize_url(href)
                bucket = classification.type
                if canon in seen_by_type[bucket]:
                    continue
                seen_by_type[bucket].add(canon)

                item = PlaywrightCollectedUrl(
                    source=self.source,
                    url=canon,
                    listing_url=listing_url,
                    classification_type=classification.type,
                    classification_reason=classification.reason,
                    suggested_action="ignore",
                )
                if classification.type == "listing":
                    item.suggested_action = "use as listing seed"
                    result.collected_listing_urls.append(item)
                    _append_rejected_example(result, "listing", canon)
                elif classification.type == "company":
                    result.company_urls.append(item)
                    _append_rejected_example(result, "company", canon)
                elif classification.type == "navigation":
                    result.navigation_urls.append(item)
                    _append_rejected_example(result, "navigation", canon)
                elif classification.type == "needs_review":
                    item.suggested_action = "import anyway"
                    result.needs_review_urls.append(item)
                    _append_rejected_example(result, "needs_review", canon)
                else:
                    result.unknown_urls.append(item)
                    _append_rejected_example(result, "unknown", canon)

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

                if scroll_n == 0 and detect_cookie_banner_visible(page):
                    retry_modal_result = handle_common_modals(page, modal_settings, context="collection")
                    modal_runs.append(retry_modal_result)
                    _merge_modal_result(result, self.source, retry_modal_result)

        page_title = ""
        try:
            page_title = page.title() or ""
        except Exception:  # noqa: BLE001
            page_title = ""

        page_specific_job_count = sum(1 for item in result.collected_urls if item.listing_url == listing_url)
        page_specific_listing_count = sum(1 for item in result.collected_listing_urls if item.listing_url == listing_url)
        page_specific_needs_review = sum(1 for item in result.needs_review_urls if item.listing_url == listing_url)
        page_specific_unknown = sum(1 for item in result.unknown_urls if item.listing_url == listing_url)
        modal_actions_payload = [
            action.model_dump(mode="json")
            for modal_run in modal_runs
            for action in list(getattr(modal_run, "actions_taken", []))
        ]
        modal_summary = {
            "handler_called": any(bool(getattr(modal_run, "handler_called", False)) for modal_run in modal_runs),
            "cookie_banner_visible_before": bool(getattr(modal_runs[0], "cookie_banner_visible_before", False)) if modal_runs else False,
            "cookie_banner_visible_after": bool(getattr(modal_runs[-1], "cookie_banner_visible_after", False)) if modal_runs else False,
            "actions_count": len(modal_actions_payload),
            "actions_success_count": sum(1 for action in modal_actions_payload if action.get("status") == "success"),
            "warnings": list(dict.fromkeys(result.modal_warnings)),
        }

        artifacts = _save_debug_artifacts(
            result.run_id,
            self.source,
            listing_url,
            all_hrefs,
            page,
            cfg,
            metadata={
                "run_id": result.run_id,
                "source": self.source,
                "keyword": criteria.keywords[0] if criteria.keywords else "",
                "query": ", ".join(criteria.keywords),
                "listing_url": listing_url,
                "final_url": getattr(page, "url", "") or "",
                "page_title": page_title,
                "status": "partial" if result.modal_warnings or result.warnings else "ok",
                "raw_link_count": len(all_hrefs),
                "job_offer_count": page_specific_job_count,
                "listing_count": page_specific_listing_count,
                "needs_review_count": page_specific_needs_review,
                "unknown_count": page_specific_unknown,
                "started_at": result.started_at,
                "finished_at": datetime.now(UTC),
                "modal_summary": modal_summary,
                "warnings": list(dict.fromkeys(result.warnings)),
                "detected_captcha": result.blocked_by_captcha,
                "detected_login_wall": result.blocked_by_login,
                "detected_blocked_page": False,
                "login_detection": login_detection_payload,
            },
            modal_actions=modal_actions_payload,
        )
        return all_hrefs, all_page_links, artifacts, ""
