from __future__ import annotations

import json
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from cv_sender.collectors.base import JobSearchCriteria
from cv_sender.collectors.playwright_base import classify_collected_url, classify_page
from cv_sender.playwright_helpers import detect_cookie_banner_visible, handle_common_modals
from cv_sender.relevance import EmergencyReactMode, match_offer_relevance

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None  # type: ignore[assignment]

_DEBUG_BASE = Path("data/debug/playwright_collectors")

ClassificationType = Literal["job_offer", "listing", "company", "navigation", "unknown", "needs_review"]
FinalAction = Literal["importable", "skipped_listing", "skipped_navigation", "skipped_irrelevant", "needs_review"]


class RawLinkWithContext(BaseModel):
    href: str = ""
    absolute_url: str = ""
    visible_text: str = ""
    parent_text_preview: str = ""
    element_role: str = ""
    source_listing_url: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)


class ClassifiedLinkEntry(BaseModel):
    url: str = ""
    visible_text: str = ""
    parent_text_preview: str = ""
    classification: ClassificationType = "unknown"
    classification_reason: str = "unknown"
    relevance_score: int = 0
    matched_keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)
    final_action: FinalAction = "needs_review"
    rejection_reasons: list[str] = Field(default_factory=list)


class JobCardCandidate(BaseModel):
    text_preview: str = ""
    links_inside: list[str] = Field(default_factory=list)
    data_attributes: dict[str, str] = Field(default_factory=dict)
    class_names_preview: str = ""
    aria_label: str = ""
    role: str = ""
    bounding_box: dict[str, float] = Field(default_factory=dict)
    matched_keywords: list[str] = Field(default_factory=list)
    salary_detected: bool = False
    location_detected: bool = False


class PlaywrightCollectorDebugReport(BaseModel):
    run_id: str
    source: str
    keyword: str
    listing_url: str
    final_url_after_redirect: str = ""
    page_title: str = ""
    started_at: datetime
    finished_at: datetime
    headless: bool
    viewport: dict[str, int] = Field(default_factory=dict)
    user_agent: str = ""
    status: str = "ok"
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    body_text_length: int = 0
    raw_html_length: int = 0
    scroll_count: int = 0
    links_before_scroll: int = 0
    links_after_scroll: int = 0
    new_links_per_scroll: list[int] = Field(default_factory=list)
    detected_cookie_banner: bool = False
    cookie_banner_handled: bool = False
    modal_actions: list[dict[str, Any]] = Field(default_factory=list)
    modal_warnings: list[str] = Field(default_factory=list)
    modal_summary: dict[str, Any] = Field(default_factory=dict)
    detected_login_wall: bool = False
    detected_captcha: bool = False
    detected_blocked_page: bool = False
    stopped_reason: str = ""
    debug_dir: str = ""
    summary_counts: dict[str, int] = Field(default_factory=dict)
    suggested_next_fix: str = ""


class PlaywrightCollectorDebugRunSummary(BaseModel):
    run_id: str
    source: str
    started_at: datetime | None = None
    headless: bool | None = None
    keyword: str = ""
    query: str = ""
    listing_url: str = ""
    final_url: str = ""
    page_title: str = ""
    status: str = "unknown"
    raw_links_count: int = 0
    job_offer_count: int = 0
    listing_count: int = 0
    needs_review_count: int = 0
    unknown_count: int = 0
    modal_actions_count: int = 0
    handler_called: bool = False
    cookie_banner_visible_before: bool = False
    cookie_banner_visible_after: bool = False
    captcha_detected: bool = False
    login_detected: bool = False
    blocked_detected: bool = False
    debug_dir: str = ""
    files: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    metadata_missing: bool = False


def _safe_json_load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _parse_debug_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_run_summary(run_dir: Path) -> PlaywrightCollectorDebugRunSummary:
    metadata_path = run_dir / "metadata.json"
    metadata = _safe_json_load(metadata_path) if metadata_path.exists() else {}
    if not isinstance(metadata, dict):
        metadata = {}

    modal_summary = metadata.get("modal_summary") or {}
    if not isinstance(modal_summary, dict):
        modal_summary = {}

    files = {path.name: str(path) for path in run_dir.iterdir() if path.is_file()}
    modal_actions_payload = _safe_json_load(run_dir / "modal_actions.json") or []
    summary_counts = metadata.get("summary_counts") or {}
    if not isinstance(summary_counts, dict):
        summary_counts = {}

    warnings = list(metadata.get("warnings") or [])
    metadata_missing = not metadata_path.exists()
    if metadata_missing:
        warnings.append("metadata.json missing")

    run_id = metadata.get("run_id") or run_dir.parent.name
    source = metadata.get("source") or run_dir.name
    started_at = _parse_debug_datetime(metadata.get("started_at") or metadata.get("timestamp"))

    return PlaywrightCollectorDebugRunSummary(
        run_id=run_id,
        source=source,
        started_at=started_at,
        headless=(bool(metadata.get("headless")) if "headless" in metadata else None),
        keyword=str(metadata.get("keyword") or ""),
        query=str(metadata.get("query") or metadata.get("keyword") or ""),
        listing_url=str(metadata.get("listing_url") or ""),
        final_url=str(metadata.get("final_url_after_redirect") or metadata.get("final_url") or ""),
        page_title=str(metadata.get("page_title") or ""),
        status=str(metadata.get("status") or ("warning" if warnings else "unknown")),
        raw_links_count=int(metadata.get("raw_link_count") or summary_counts.get("raw_links_found") or 0),
        job_offer_count=int(metadata.get("job_offer_count") or summary_counts.get("job_offer") or 0),
        listing_count=int(metadata.get("listing_count") or summary_counts.get("listing") or 0),
        needs_review_count=int(metadata.get("needs_review_count") or summary_counts.get("needs_review") or 0),
        unknown_count=int(metadata.get("unknown_count") or summary_counts.get("unknown") or 0),
        modal_actions_count=int(modal_summary.get("actions_count") or len(modal_actions_payload or [])),
        handler_called=bool(modal_summary.get("handler_called", False)),
        cookie_banner_visible_before=bool(modal_summary.get("cookie_banner_visible_before", False)),
        cookie_banner_visible_after=bool(modal_summary.get("cookie_banner_visible_after", False)),
        captcha_detected=bool(metadata.get("detected_captcha", False)),
        login_detected=bool(metadata.get("detected_login_wall", False)),
        blocked_detected=bool(metadata.get("detected_blocked_page", False)),
        debug_dir=str(run_dir),
        files=files,
        warnings=list(dict.fromkeys(warnings)),
        metadata_missing=metadata_missing,
    )


def discover_playwright_debug_runs(limit: int = 10, base_dir: Path | None = None) -> list[PlaywrightCollectorDebugRunSummary]:
    debug_base = base_dir or _DEBUG_BASE
    if not debug_base.exists():
        return []

    runs: list[PlaywrightCollectorDebugRunSummary] = []
    for run_root in debug_base.iterdir():
        if not run_root.is_dir():
            continue
        for source_dir in run_root.iterdir():
            if not source_dir.is_dir():
                continue
            runs.append(_build_run_summary(source_dir))

    runs.sort(
        key=lambda item: item.started_at or datetime.fromtimestamp(Path(item.debug_dir).stat().st_mtime, tz=UTC),
        reverse=True,
    )
    return runs[:limit]


def _collector_for_source(source: str):
    src = source.lower().strip()
    if src == "justjoin":
        from cv_sender.collectors.playwright_justjoin import PlaywrightJustJoinCollector  # noqa: PLC0415

        return PlaywrightJustJoinCollector()
    if src == "rocketjobs":
        from cv_sender.collectors.playwright_rocketjobs import PlaywrightRocketJobsCollector  # noqa: PLC0415

        return PlaywrightRocketJobsCollector()
    if src == "pracuj":
        from cv_sender.collectors.playwright_pracuj import PlaywrightPracujCollector  # noqa: PLC0415

        return PlaywrightPracujCollector()
    if src == "nofluffjobs":
        from cv_sender.collectors.playwright_nofluffjobs import PlaywrightNoFluffJobsCollector  # noqa: PLC0415

        return PlaywrightNoFluffJobsCollector()
    raise ValueError(f"Unsupported source for debug: {source!r}")


def _safe_preview(text: str, max_len: int = 280) -> str:
    compact = " ".join((text or "").split())
    return compact[:max_len]


def _abs_url(base_url: str, href: str) -> str:
    from urllib.parse import urljoin

    if not href:
        return ""
    href = href.strip()
    if href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return ""
    return urljoin(base_url, href)


def extract_all_links_with_context(page: Any, source_listing_url: str) -> list[RawLinkWithContext]:
    """Collect links and clickable elements with context from the current page."""
    js = """
    () => {
      const selectors = [
        'a[href]',
        '[role="link"]',
        '[onclick]',
        '[data-testid]',
        '[data-href]',
        '[data-url]',
        'button'
      ];
      const nodes = Array.from(document.querySelectorAll(selectors.join(',')));
      const out = [];
      for (const el of nodes) {
        const attrHref = el.getAttribute('href') || el.getAttribute('data-href') || el.getAttribute('data-url') || '';
        const nestedA = el.querySelector('a[href]');
        const nestedHref = nestedA ? (nestedA.getAttribute('href') || '') : '';
        const href = attrHref || nestedHref || '';
        const text = (el.innerText || el.textContent || '').trim();
        const role = el.getAttribute('role') || el.tagName.toLowerCase();
        const parent = el.closest('article,li,section,div');
        const parentText = parent ? (parent.innerText || parent.textContent || '') : '';
        const attrs = {};
        for (const a of Array.from(el.attributes || [])) {
          if (a && a.name && (a.name.startsWith('data-') || a.name === 'aria-label' || a.name === 'role' || a.name === 'class')) {
            attrs[a.name] = String(a.value || '');
          }
        }
        out.push({
          href,
          visible_text: text,
          parent_text_preview: parentText,
          element_role: role,
          attributes: attrs,
        });
      }
      return out;
    }
    """
    rows = page.evaluate(js) or []
    items: list[RawLinkWithContext] = []
    seen: set[tuple[str, str, str]] = set()

    for row in rows:
        href = str(row.get("href") or "").strip()
        absolute = _abs_url(source_listing_url, href)
        visible_text = _safe_preview(str(row.get("visible_text") or ""), 180)
        parent_text = _safe_preview(str(row.get("parent_text_preview") or ""), 280)
        element_role = str(row.get("element_role") or "")
        attributes = {str(k): str(v) for k, v in dict(row.get("attributes") or {}).items()}
        key = (absolute, visible_text, parent_text)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            RawLinkWithContext(
                href=href,
                absolute_url=absolute,
                visible_text=visible_text,
                parent_text_preview=parent_text,
                element_role=element_role,
                source_listing_url=source_listing_url,
                attributes=attributes,
            )
        )
    return items


def find_job_card_candidates(page: Any, criteria: JobSearchCriteria) -> list[JobCardCandidate]:
    """Find card-like elements that may represent offers even without direct href."""
    js = """
    () => {
      const selectors = [
        'article', 'li', 'section',
        'div[class*="job"]', 'div[class*="offer"]', 'div[class*="card"]', 'div[class*="listing"]',
        '[data-testid]', '[role="article"]'
      ];
      const nodes = Array.from(document.querySelectorAll(selectors.join(',')));
      const out = [];
      for (const el of nodes) {
        const text = (el.innerText || el.textContent || '').trim();
        if (!text || text.length < 40) continue;
        const linksInside = Array.from(el.querySelectorAll('a[href]')).map(a => a.getAttribute('href') || '').filter(Boolean);
        const attrs = {};
        for (const a of Array.from(el.attributes || [])) {
          if (a && a.name && (a.name.startsWith('data-') || a.name === 'aria-label' || a.name === 'role' || a.name === 'class')) {
            attrs[a.name] = String(a.value || '');
          }
        }
        const box = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
        out.push({
          text_preview: text,
          links_inside: linksInside,
          data_attributes: attrs,
          class_names_preview: el.className ? String(el.className) : '',
          aria_label: el.getAttribute('aria-label') || '',
          role: el.getAttribute('role') || el.tagName.toLowerCase(),
          bounding_box: box ? {
            x: Number(box.x || 0),
            y: Number(box.y || 0),
            width: Number(box.width || 0),
            height: Number(box.height || 0)
          } : {},
        });
      }
      return out;
    }
    """
    rows = page.evaluate(js) or []
    role_terms = ["react", "frontend", "developer", "engineer", "programista"]
    location_terms = ["remote", "warsz", "krak", "wrocl", "wroc", "pozn", "gdansk", "hybrid", "zdaln"]
    salary_re = re.compile(r"(pln|zl|\b\d{1,3}\s?\d{3}\b|\d+\s?(k|000))", re.IGNORECASE)

    desired_terms = [x.lower() for x in criteria.keywords + criteria.technologies if x]
    desired_terms.extend(role_terms)

    out: list[JobCardCandidate] = []
    for row in rows:
        text_preview = _safe_preview(str(row.get("text_preview") or ""), 360)
        low = text_preview.lower()
        matched = [t for t in desired_terms if t and t in low]
        has_company_hint = bool(re.search(r"\b(sp\. z o\.o\.|s\.a\.|llc|inc|gmbh|ltd)\b", low))
        salary_detected = bool(salary_re.search(low))
        location_detected = any(tok in low for tok in location_terms)
        if not matched and not has_company_hint and not salary_detected and not location_detected:
            continue

        out.append(
            JobCardCandidate(
                text_preview=text_preview,
                links_inside=[str(x) for x in list(row.get("links_inside") or [])[:10]],
                data_attributes={str(k): str(v) for k, v in dict(row.get("data_attributes") or {}).items()},
                class_names_preview=_safe_preview(str(row.get("class_names_preview") or ""), 140),
                aria_label=_safe_preview(str(row.get("aria_label") or ""), 120),
                role=str(row.get("role") or ""),
                bounding_box={
                    str(k): float(v)
                    for k, v in dict(row.get("bounding_box") or {}).items()
                    if isinstance(v, (int, float))
                },
                matched_keywords=sorted(set(matched))[:12],
                salary_detected=salary_detected,
                location_detected=location_detected,
            )
        )

    return out[:120]


def classify_links_with_reasons(
    links: list[RawLinkWithContext],
    source: str,
    criteria: JobSearchCriteria,
    *,
    emergency_mode: EmergencyReactMode | None = None,
) -> list[ClassifiedLinkEntry]:
    out: list[ClassifiedLinkEntry] = []
    mode = emergency_mode or EmergencyReactMode()

    for item in links:
        url = item.absolute_url or ""
        if not url:
            continue

        c = classify_collected_url(source, url)
        relevance_score = 0
        matched_keywords: list[str] = []
        negative_keywords: list[str] = []
        rejection_reasons: list[str] = []

        if c.type == "job_offer":
            rel = match_offer_relevance(
                {
                    "source": source,
                    "url": url,
                    "title_preview": item.visible_text,
                    "raw_text_preview": item.parent_text_preview,
                },
                criteria,
                emergency_mode=mode,
            )
            relevance_score = rel.score
            matched_keywords = list(dict.fromkeys(rel.matched_keywords + rel.matched_technologies))
            negative_keywords = rel.rejected_keywords
            rejection_reasons.extend(rel.reasons)
            if rel.decision == "relevant":
                final_action: FinalAction = "importable"
            elif rel.decision == "irrelevant":
                final_action = "skipped_irrelevant"
            else:
                final_action = "needs_review"
        elif c.type == "listing":
            final_action = "skipped_listing"
            rejection_reasons.append("listing_url")
        elif c.type in ("navigation", "company"):
            final_action = "skipped_navigation"
            rejection_reasons.append("navigation_or_company_url")
        else:
            final_action = "needs_review"
            rejection_reasons.append("non_importable_url_shape")

        rejection_reasons.insert(0, c.reason)

        out.append(
            ClassifiedLinkEntry(
                url=url,
                visible_text=item.visible_text,
                parent_text_preview=item.parent_text_preview,
                classification=c.type,
                classification_reason=c.reason,
                relevance_score=relevance_score,
                matched_keywords=matched_keywords,
                negative_keywords=negative_keywords,
                final_action=final_action,
                rejection_reasons=list(dict.fromkeys(rejection_reasons)),
            )
        )

    return out


def classification_summary_counts(classified_links: list[ClassifiedLinkEntry]) -> dict[str, int]:
    counts = {
        "raw_links_found": len(classified_links),
        "job_offer": 0,
        "listing": 0,
        "navigation_company": 0,
        "unknown": 0,
        "needs_review": 0,
    }
    for row in classified_links:
        if row.classification == "job_offer":
            counts["job_offer"] += 1
        elif row.classification == "listing":
            counts["listing"] += 1
        elif row.classification in ("navigation", "company"):
            counts["navigation_company"] += 1
        elif row.classification == "needs_review":
            counts["needs_review"] += 1
        else:
            counts["unknown"] += 1
    return counts


def suggested_next_fix(
    *,
    raw_links_count: int,
    job_offer_count: int,
    listing_count: int,
    job_card_candidates_count: int,
    links_before_scroll: int,
    new_links_per_scroll: list[int],
    login_or_captcha_or_blocked: bool,
) -> str:
    if login_or_captcha_or_blocked:
        return "Do not bypass; use manual listing URL or bookmarklet."
    if raw_links_count > 0 and job_offer_count == 0:
        return "URL classifier likely too strict or source URL pattern changed."
    if job_card_candidates_count > 0 and raw_links_count == 0:
        return "Cards may use client-side navigation; inspect onclick/router/data attributes."
    if raw_links_count > 0 and listing_count >= max(1, int(raw_links_count * 0.6)):
        return "Listing page exposes SEO/category links before job cards; collector should target job card containers."
    if links_before_scroll > 0 and not any(delta > 0 for delta in new_links_per_scroll):
        return "Infinite scroll may require different container scroll or button."
    return "Inspect top job card candidates and rejected URLs to refine extraction selectors deterministically."


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _cookie_banner_detected(text: str) -> bool:
    low = text.lower()
    return any(tok in low for tok in ["cookie", "cookies", "consent", "rodo"])


def _build_markdown_report(
    report: PlaywrightCollectorDebugReport,
    classified_links: list[ClassifiedLinkEntry],
    cards: list[JobCardCandidate],
) -> str:
    summary = report.summary_counts
    top_offers = [x for x in classified_links if x.classification == "job_offer"][:10]
    top_listings = [x for x in classified_links if x.classification == "listing"][:10]
    top_cards = cards[:10]

    lines: list[str] = []
    lines.append("# Playwright Collector Debug Report")
    lines.append("")
    lines.append("## Source")
    lines.append(report.source)
    lines.append("")
    lines.append("## Listing URL")
    lines.append(report.listing_url)
    lines.append("")
    lines.append("## Page title")
    lines.append(report.page_title or "(empty)")
    lines.append("")
    lines.append("## Final URL")
    lines.append(report.final_url_after_redirect or "(empty)")
    lines.append("")
    lines.append("## Scroll behavior")
    lines.append(f"- links before scroll: {report.links_before_scroll}")
    for idx, delta in enumerate(report.new_links_per_scroll, start=1):
        lines.append(f"- scroll {idx}: +{delta} new links")
    lines.append(f"- links after scroll: {report.links_after_scroll}")
    lines.append(f"- stopped reason: {report.stopped_reason or 'max_scrolls_reached'}")
    lines.append("")
    lines.append("## Link classification summary")
    lines.append(f"- raw links found: {summary.get('raw_links_found', 0)}")
    lines.append(f"- job_offer: {summary.get('job_offer', 0)}")
    lines.append(f"- listing: {summary.get('listing', 0)}")
    lines.append(f"- navigation/company: {summary.get('navigation_company', 0)}")
    lines.append(f"- unknown: {summary.get('unknown', 0)}")
    lines.append(f"- needs_review: {summary.get('needs_review', 0)}")
    lines.append("")
    lines.append("## Top job offer candidates")
    if top_offers:
        for row in top_offers:
            lines.append(f"- {row.url} | {row.visible_text[:90]} | score={row.relevance_score}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Top rejected listing URLs")
    if top_listings:
        for row in top_listings:
            lines.append(f"- {row.url} | reason={row.classification_reason}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Potential job cards without valid offer URLs")
    if top_cards:
        for row in top_cards:
            lines.append(f"- {row.text_preview[:120]} | links_inside={len(row.links_inside)}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Warnings")
    warnings = list(report.warnings)
    if report.links_after_scroll > 0 and report.summary_counts.get("job_offer", 0) == 0:
        warnings.append("Found many raw links but zero job_offer URLs.")
    if cards and report.links_after_scroll == 0:
        warnings.append("Found card-like elements but no href links.")
    if report.summary_counts.get("listing", 0) > report.summary_counts.get("job_offer", 0):
        warnings.append("Listing links dominate; collector may be focusing on category links first.")
    if report.new_links_per_scroll and not any(x > 0 for x in report.new_links_per_scroll):
        warnings.append("Scroll did not load new items.")
    if report.detected_cookie_banner:
        warnings.append("Cookie modal may block interaction.")
    if report.detected_login_wall or report.detected_captcha or report.detected_blocked_page:
        warnings.append("Login/CAPTCHA/blocked state detected. Do not bypass protections.")
    if warnings:
        for msg in dict.fromkeys(warnings):
            lines.append(f"- {msg}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Suggested next fix")
    lines.append(report.suggested_next_fix)

    return "\n".join(lines).strip() + "\n"


def debug_collect_source(
    source: str,
    criteria: JobSearchCriteria,
    listing_url: str | None = None,
    *,
    headless: bool = False,
    max_scrolls: int = 5,
    page_timeout_ms: int = 30_000,
    save_html: bool = False,
    save_screenshot: bool = True,
    save_trace: bool = False,
    modal_settings_override: dict[str, Any] | None = None,
) -> PlaywrightCollectorDebugReport:
    """Run a single-source Playwright debug session and write detailed artifacts."""
    if sync_playwright is None:
        raise RuntimeError("Playwright is not installed. Run: pip install playwright && playwright install chromium")

    collector = _collector_for_source(source)
    src = collector.source
    keyword = criteria.keywords[0] if criteria.keywords else ""
    selected_listing_url = listing_url or (collector.build_search_urls(criteria) or [""])[0]
    if not selected_listing_url:
        raise ValueError(f"Cannot build listing URL for source: {source}")

    run_id = str(uuid.uuid4())
    started_at = datetime.now(UTC)
    run_dir = _DEBUG_BASE / run_id / src
    run_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    errors: list[str] = []
    final_url = ""
    page_title = ""
    user_agent = ""
    body_text = ""
    raw_html = ""
    links_before: list[RawLinkWithContext] = []
    all_links_after_scroll: list[RawLinkWithContext] = []
    cards: list[JobCardCandidate] = []
    new_links_per_scroll: list[int] = []
    stopped_reason = "max_scrolls_reached"
    scroll_count = 0
    modal_actions: list[dict[str, Any]] = []
    modal_warnings: list[str] = []
    cookie_banner_handled = False
    modal_summary: dict[str, Any] = {}
    modal_settings: Any = None

    try:
        from cv_sender.config import load_settings  # noqa: PLC0415

        modal_settings = load_settings()
        if modal_settings_override:
            modal_cfg = modal_settings.playwright.modals.model_copy(update=modal_settings_override)
            modal_settings.playwright = modal_settings.playwright.model_copy(update={"modals": modal_cfg})
    except Exception:  # noqa: BLE001
        modal_settings = modal_settings_override or None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1440, "height": 1080})
        page = context.new_page()

        if save_trace:
            try:
                context.tracing.start(screenshots=True, snapshots=True, sources=False)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"trace_start_failed: {exc}")

        try:
            page.set_default_timeout(page_timeout_ms)
            page.goto(selected_listing_url, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=min(page_timeout_ms, 12000))
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(400)

            if save_screenshot:
                page.screenshot(path=str(run_dir / "screenshot_initial.png"), full_page=False)

            modal_result = handle_common_modals(page, modal_settings, context="debug")
            modal_actions = [
                a.model_dump(mode="json")
                for a in modal_result.actions_taken
            ]
            modal_warnings = list(modal_result.warnings)
            cookie_banner_handled = any(
                a.type in {"cookie_accept", "cookie_reject", "cookie_close"} and a.status == "success"
                for a in modal_result.actions_taken
            )
            modal_summary = {
                "handler_called": bool(modal_result.handler_called),
                "cookie_banner_visible_before": bool(modal_result.cookie_banner_visible_before),
                "cookie_banner_visible_after": bool(modal_result.cookie_banner_visible_after),
                "actions_count": len(modal_actions),
                "actions_success_count": sum(1 for action in modal_actions if action.get("status") == "success"),
                "warnings": list(modal_warnings),
            }
            warnings.extend(modal_result.warnings)

            if save_screenshot:
                page.wait_for_timeout(350)
                page.screenshot(path=str(run_dir / "screenshot_after_modals.png"), full_page=False)

            if modal_result.blocked_by_captcha:
                warnings.append("CAPTCHA detected; debug run stopped without bypass.")
                stopped_reason = "blocked_by_captcha"
            if modal_result.blocked_by_login:
                warnings.append("Login wall detected; debug run stopped without bypass.")
                stopped_reason = "blocked_by_login"

            if modal_result.blocked_by_captcha or modal_result.blocked_by_login:
                final_url = page.url
                page_title = page.title() or ""
                user_agent = str(page.evaluate("() => navigator.userAgent") or "")
                body_text = page.inner_text("body") or ""
                raw_html = page.content() or ""
                raise RuntimeError("Protected page detected; stopped safely.")

            final_url = page.url
            page_title = page.title() or ""
            user_agent = str(page.evaluate("() => navigator.userAgent") or "")
            body_text = page.inner_text("body") or ""
            raw_html = page.content() or ""

            links_before = extract_all_links_with_context(page, selected_listing_url)
            all_links_after_scroll = list(links_before)
            seen_link_keys = {(x.absolute_url, x.visible_text, x.parent_text_preview) for x in links_before}

            no_new_consecutive = 0
            for idx in range(max_scrolls):
                scroll_count = idx + 1
                page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.85));")
                time.sleep(1.0)
                page.wait_for_timeout(250)

                if idx == 0 and detect_cookie_banner_visible(page):
                    retry_modal_result = handle_common_modals(page, modal_settings, context="debug")
                    retry_actions = [action.model_dump(mode="json") for action in retry_modal_result.actions_taken]
                    modal_actions.extend(retry_actions)
                    modal_warnings.extend(retry_modal_result.warnings)
                    warnings.extend(retry_modal_result.warnings)
                    modal_summary = {
                        "handler_called": bool(modal_summary.get("handler_called", False) or retry_modal_result.handler_called),
                        "cookie_banner_visible_before": bool(modal_summary.get("cookie_banner_visible_before", False)),
                        "cookie_banner_visible_after": bool(retry_modal_result.cookie_banner_visible_after),
                        "actions_count": len(modal_actions),
                        "actions_success_count": sum(1 for action in modal_actions if action.get("status") == "success"),
                        "warnings": list(dict.fromkeys(modal_warnings)),
                    }

                current = extract_all_links_with_context(page, selected_listing_url)
                added = 0
                for item in current:
                    key = (item.absolute_url, item.visible_text, item.parent_text_preview)
                    if key in seen_link_keys:
                        continue
                    seen_link_keys.add(key)
                    all_links_after_scroll.append(item)
                    added += 1
                new_links_per_scroll.append(added)

                if added == 0:
                    no_new_consecutive += 1
                else:
                    no_new_consecutive = 0

                if no_new_consecutive >= 2:
                    stopped_reason = "no_new_links_after_scroll"
                    break

            if save_screenshot:
                page.screenshot(path=str(run_dir / "screenshot_after_scroll.png"), full_page=False)

            if save_html:
                (run_dir / "html_preview.html").write_text(raw_html[:900_000], encoding="utf-8")

            cards = find_job_card_candidates(page, criteria)

        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
        finally:
            if save_trace:
                try:
                    context.tracing.stop(path=str(run_dir / "trace.zip"))
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"trace_stop_failed: {exc}")
            context.close()
            browser.close()

    finished_at = datetime.now(UTC)

    classified_links = classify_links_with_reasons(all_links_after_scroll, src, criteria)
    summary = classification_summary_counts(classified_links)

    page_class = classify_page(body_text)
    detected_login_wall = page_class == "login_wall"
    detected_captcha = page_class == "captcha"
    detected_blocked_page = page_class == "blocked"
    detected_cookie_banner = _cookie_banner_detected(body_text)

    if not modal_summary:
        modal_summary = {
            "handler_called": False,
            "cookie_banner_visible_before": False,
            "cookie_banner_visible_after": False,
            "actions_count": 0,
            "actions_success_count": 0,
            "warnings": list(dict.fromkeys(modal_warnings)),
        }

    if summary["raw_links_found"] > 0 and summary["job_offer"] == 0:
        warnings.append("Found many raw links but zero job_offer URLs.")
    if cards and summary["raw_links_found"] == 0:
        warnings.append("Found card-like elements but no href links.")
    if new_links_per_scroll and not any(x > 0 for x in new_links_per_scroll):
        warnings.append("Scroll did not load new items.")

    suggestion = suggested_next_fix(
        raw_links_count=summary["raw_links_found"],
        job_offer_count=summary["job_offer"],
        listing_count=summary["listing"],
        job_card_candidates_count=len(cards),
        links_before_scroll=len(links_before),
        new_links_per_scroll=new_links_per_scroll,
        login_or_captcha_or_blocked=detected_login_wall or detected_captcha or detected_blocked_page,
    )

    status = "ok"
    if errors:
        status = "failed"
    elif warnings:
        status = "partial"

    report = PlaywrightCollectorDebugReport(
        run_id=run_id,
        source=src,
        keyword=keyword,
        listing_url=selected_listing_url,
        final_url_after_redirect=final_url,
        page_title=page_title,
        started_at=started_at,
        finished_at=finished_at,
        headless=headless,
        viewport={"width": 1440, "height": 1080},
        user_agent=user_agent,
        status=status,
        warnings=list(dict.fromkeys(warnings)),
        errors=errors,
        body_text_length=len(body_text),
        raw_html_length=len(raw_html),
        scroll_count=scroll_count,
        links_before_scroll=len(links_before),
        links_after_scroll=len(all_links_after_scroll),
        new_links_per_scroll=new_links_per_scroll,
        detected_cookie_banner=detected_cookie_banner,
        cookie_banner_handled=cookie_banner_handled,
        modal_actions=modal_actions,
        modal_warnings=modal_warnings,
        modal_summary=modal_summary,
        detected_login_wall=detected_login_wall,
        detected_captcha=detected_captcha,
        detected_blocked_page=detected_blocked_page,
        stopped_reason=stopped_reason,
        debug_dir=str(run_dir),
        summary_counts=summary,
        suggested_next_fix=suggestion,
    )

    _write_json(run_dir / "metadata.json", report.model_dump(mode="json"))
    _write_json(run_dir / "modal_actions.json", modal_actions)
    _write_json(run_dir / "links.json", [x.model_dump(mode="json") for x in all_links_after_scroll])
    _write_json(run_dir / "classified_links.json", [x.model_dump(mode="json") for x in classified_links])
    _write_json(run_dir / "job_card_candidates.json", [x.model_dump(mode="json") for x in cards])

    if save_html and not (run_dir / "html_preview.html").exists():
        (run_dir / "html_preview.html").write_text(raw_html[:900_000], encoding="utf-8")

    md = _build_markdown_report(report, classified_links, cards)
    (run_dir / "debug_report.md").write_text(md, encoding="utf-8")

    return report
