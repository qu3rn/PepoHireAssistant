"""Deep offer detail extraction and completeness checks.

This module enriches existing Offer records when imported data is shallow.
It never bypasses CAPTCHAs/login walls and never submits applications.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cv_sender.apply_queue import sync_all_queue_items_from_offers
from cv_sender.config import load_settings
from cv_sender.extractors import get_extractor
from cv_sender.extractors.base import clean_description, normalize_salary, normalize_technologies
from cv_sender.extractors.generic import GenericExtractor
from cv_sender.llm import get_llm_score
from cv_sender.models import (
    DeepExtractionBatchResult,
    DeepExtractionResult,
    DeepExtractionStatus,
    Offer,
    OfferCompletenessResult,
)
from cv_sender.playwright_helpers import detect_login_detection, handle_common_modals
from cv_sender.scorer import score_offer
from cv_sender.storage import get_offer_by_id, update_offer
from cv_sender.title_utils import normalize_company_name, normalize_offer_title

logger = logging.getLogger(__name__)

_BLOCK_TOKENS = ("captcha", "recaptcha", "hcaptcha", "i'm not a robot", "nie jestem robotem")
_TECH_HINTS = {
    "react", "typescript", "javascript", "python", "django", "flask", "fastapi", "node", "node.js",
    "next", "next.js", "vue", "angular", "java", "spring", "kotlin", "swift", "go", "golang",
    "aws", "azure", "gcp", "docker", "kubernetes", "sql", "postgres", "mysql", "mongodb", "redis",
    "graphql", "rest", "c#", ".net", "php", "laravel", "ruby", "rails",
}


@dataclass
class _PageExtractionPayload:
    html: str = ""
    visible_text: str = ""
    extractor_used: str = ""
    modal_summary: dict[str, Any] | None = None
    blocked: bool = False
    blocked_reason: str = ""
    screenshot_path: str = ""


@dataclass
class _MergeOutcome:
    offer: Offer
    fields_updated: list[str]
    merge_diff: dict[str, Any]


def _is_unknown_company(value: str) -> bool:
    low = (value or "").strip().lower()
    return low in {"", "unknown", "unknown company", "n/a", "na", "none"}


def _looks_slug_like(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return True
    if re.search(r"https?://", text, flags=re.IGNORECASE):
        return True
    if "/" in text and " " not in text:
        return True
    if re.fullmatch(r"[a-z0-9\-_,.]+", text) and ("-" in text or "_" in text):
        return True
    return False


def _offer_field_snapshot(offer: Offer) -> dict[str, object]:
    return {
        "title": offer.title,
        "company": offer.company,
        "salary_min": offer.salary_min,
        "salary_max": offer.salary_max,
        "currency": offer.currency,
        "salary_raw_text": offer.salary_raw_text,
        "salary_confidence": offer.salary_confidence,
        "location": offer.location,
        "contract": offer.contract,
        "technologies": list(offer.technologies),
        "description": offer.description,
        "extraction_source": offer.extraction_source,
        "extraction_confidence": offer.extraction_confidence,
    }


def _salary_raw_from_text(text: str) -> str:
    if not text:
        return ""
    patterns = [
        r"\b\d{1,3}(?:[\s,.]\d{3})+(?:\s*(?:PLN|EUR|USD|GBP|zł|zl))?\b",
        r"\b\d{4,6}(?:\s*(?:PLN|EUR|USD|GBP|zł|zl))?\b",
    ]
    for pat in patterns:
        match = re.search(pat, text, flags=re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return ""


def _extract_technologies_from_text(text: str) -> list[str]:
    low = (text or "").lower()
    found: list[str] = []
    for token in sorted(_TECH_HINTS):
        token_re = re.escape(token)
        if re.search(rf"\b{token_re}\b", low):
            found.append(token)
    normalized = normalize_technologies(found)
    pretty_map = {
        "node.js": "Node.js",
        "next.js": "Next.js",
        "typescript": "TypeScript",
        "javascript": "JavaScript",
        "react": "React",
        "postgres": "Postgres",
        ".net": ".NET",
        "c#": "C#",
    }
    return [pretty_map.get(item.lower(), item.title()) for item in normalized]


def _trim_noise(text: str) -> str:
    if not text:
        return ""
    cleaned = clean_description(text)
    cleaned = re.sub(r"\b(cookie|cookies|privacy policy|newsletter|accept all|reject all)\b", " ", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()[:12000]


def is_offer_incomplete(offer: Offer) -> OfferCompletenessResult:
    """Return completeness analysis for an offer.

    Score is 0-100 and combines presence + quality checks.
    """

    missing_fields: list[str] = []
    weak_fields: list[str] = []
    reasons: list[str] = []

    score = 100

    if not (offer.title or "").strip():
        missing_fields.append("title")
        reasons.append("Title is missing.")
        score -= 15
    else:
        if _looks_slug_like(offer.title):
            weak_fields.append("title")
            reasons.append("Title looks slug-like or URL-derived.")
            score -= 8
        if offer.extraction_confidence and offer.extraction_confidence < 0.25:
            if "title" not in weak_fields:
                weak_fields.append("title")
            reasons.append("Low extraction confidence for title.")
            score -= 5

    if _is_unknown_company(offer.company):
        missing_fields.append("company")
        reasons.append("Company is missing or unknown.")
        score -= 10

    if not (offer.salary_raw_text or "").strip() and offer.salary_min is None and offer.salary_max is None:
        missing_fields.append("salary")
        reasons.append("Salary information is missing.")
        score -= 20
    elif offer.salary_confidence and offer.salary_confidence < 0.35:
        weak_fields.append("salary")
        reasons.append("Salary confidence is low.")
        score -= 10

    if offer.salary_min is None and offer.salary_max is None:
        missing_fields.append("normalized_salary")
        reasons.append("Normalized salary range is missing.")
        score -= 10

    if not (offer.location or "").strip():
        missing_fields.append("location")
        reasons.append("Location is missing.")
        score -= 10

    if not (offer.contract or "").strip():
        missing_fields.append("contract")
        reasons.append("Contract type is missing.")
        score -= 10

    if not offer.technologies:
        missing_fields.append("technologies")
        reasons.append("Technologies are missing.")
        score -= 15
    elif len(offer.technologies) < 2:
        weak_fields.append("technologies")
        reasons.append("Technologies are too sparse (<2).")
        score -= 8

    desc_len = len((offer.description or "").strip())
    if desc_len == 0:
        missing_fields.append("description")
        reasons.append("Description is missing.")
        score -= 10
    elif desc_len < 300:
        weak_fields.append("description")
        reasons.append("Description is too short (<300 chars).")
        score -= 6

    score = max(0, min(100, score))
    is_incomplete = bool(missing_fields or weak_fields)

    return OfferCompletenessResult(
        is_incomplete=is_incomplete,
        missing_fields=sorted(set(missing_fields)),
        weak_fields=sorted(set(weak_fields)),
        score=score,
        reasons=list(dict.fromkeys(reasons)),
    )


def _extract_with_playwright(offer: Offer, run_dir: Path) -> _PageExtractionPayload:
    payload = _PageExtractionPayload()
    settings = load_settings()

    from cv_sender.browser import browser_session, navigate  # noqa: PLC0415

    with browser_session(headless=True) as (page, _browser):
        navigate(page, offer.url)

        modal_result = handle_common_modals(page, settings=settings, context="deep_extraction")
        payload.modal_summary = modal_result.model_dump(mode="json")

        body_text = ""
        try:
            body_text = page.inner_text("body") or ""
        except Exception:  # noqa: BLE001
            body_text = ""

        has_block_token = any(token in body_text.lower() for token in _BLOCK_TOKENS)
        login_detection = detect_login_detection(page)
        blocked = modal_result.blocked_by_captcha or modal_result.blocked_by_login or has_block_token or login_detection.login_wall_detected
        if blocked:
            payload.blocked = True
            if modal_result.blocked_by_captcha or has_block_token:
                payload.blocked_reason = "captcha_detected"
            elif modal_result.blocked_by_login or login_detection.login_wall_detected:
                payload.blocked_reason = "login_wall_detected"
            else:
                payload.blocked_reason = "page_blocked"
            return payload

        try:
            payload.html = page.content() or ""
        except Exception:  # noqa: BLE001
            payload.html = ""

        visible_text = ""
        for selector in ("main", "article", "[role='main']", "body"):
            try:
                text = page.inner_text(selector) or ""
            except Exception:  # noqa: BLE001
                text = ""
            if text and len(text.strip()) > len(visible_text.strip()):
                visible_text = text
        payload.visible_text = _trim_noise(visible_text)

        screenshot_path = run_dir / "screenshot.png"
        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
            payload.screenshot_path = str(screenshot_path)
        except Exception:  # noqa: BLE001
            payload.screenshot_path = ""

    return payload


def _extract_without_playwright(offer: Offer) -> _PageExtractionPayload:
    payload = _PageExtractionPayload()
    try:
        from cv_sender.extractors import _fetch_html  # noqa: PLC0415

        payload.html = _fetch_html(offer.url) or ""
    except Exception:  # noqa: BLE001
        payload.html = ""
    return payload


def _extract_raw_fields(offer: Offer, payload: _PageExtractionPayload) -> tuple[dict[str, Any], str, list[str]]:
    warnings: list[str] = []

    extractor = get_extractor(offer.url)
    extractor_name = getattr(extractor, "source", "unknown")
    draft = None

    if payload.html:
        try:
            draft = extractor.extract(offer.url, payload.html)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Primary extractor failed: {exc}")
            draft = None

    if draft is None or not draft.title:
        try:
            draft = GenericExtractor().extract(offer.url, payload.html)
            extractor_name = "generic_playwright_fallback"
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Generic extractor failed: {exc}")
            draft = None

    if draft is None:
        draft_data: dict[str, Any] = {}
    else:
        draft_data = {
            "title": draft.title,
            "company": draft.company,
            "salary_min": draft.salary_min,
            "salary_max": draft.salary_max,
            "currency": draft.currency,
            "location": draft.location,
            "contract": draft.contract,
            "technologies": list(draft.technologies),
            "description": draft.description,
            "extraction_source": draft.extraction_source,
            "extraction_confidence": draft.extraction_confidence,
            "warnings": list(draft.extraction_warnings),
        }
        warnings.extend(draft.extraction_warnings)

    text_blob = payload.visible_text or ""
    if text_blob:
        if not draft_data.get("salary_min") and not draft_data.get("salary_max"):
            salary_raw = _salary_raw_from_text(text_blob)
            if salary_raw:
                parsed = normalize_salary(salary_raw)
                if parsed is not None:
                    draft_data["salary_min"] = parsed
                    draft_data["salary_max"] = parsed
                draft_data["salary_raw_text"] = salary_raw

        if not draft_data.get("technologies"):
            draft_data["technologies"] = _extract_technologies_from_text(text_blob)

        if not draft_data.get("description") or len(str(draft_data.get("description") or "")) < 300:
            draft_data["description"] = text_blob

    # Normalization
    draft_data["title"] = normalize_offer_title(str(draft_data.get("title") or ""), source=offer.source, url=offer.url)
    draft_data["company"] = normalize_company_name(str(draft_data.get("company") or ""))
    draft_data["description"] = _trim_noise(str(draft_data.get("description") or ""))
    draft_data["technologies"] = normalize_technologies(draft_data.get("technologies") or [])

    raw_salary_text = str(draft_data.get("salary_raw_text") or "")
    if not raw_salary_text:
        raw_salary_text = _salary_raw_from_text(" ".join([str(draft_data.get("description") or ""), text_blob]))
    if raw_salary_text:
        draft_data["salary_raw_text"] = raw_salary_text

    if draft_data.get("salary_min") is not None and draft_data.get("salary_max") is None:
        draft_data["salary_max"] = draft_data.get("salary_min")

    return draft_data, extractor_name, list(dict.fromkeys(warnings))


def _should_update_title(existing: Offer, new_title: str, force: bool, new_confidence: float) -> bool:
    if not new_title:
        return False
    old_title = (existing.title or "").strip()
    if not old_title:
        return True
    if _looks_slug_like(old_title) and not _looks_slug_like(new_title):
        return True
    if force and new_confidence >= (existing.extraction_confidence or 0.0):
        return True
    return False


def _merge_offer(existing: Offer, extracted: dict[str, Any], *, force: bool) -> _MergeOutcome:
    updates: dict[str, Any] = {}
    fields_updated: list[str] = []
    merge_diff: dict[str, Any] = {}

    new_confidence = float(extracted.get("extraction_confidence") or 0.0)

    new_title = str(extracted.get("title") or "").strip()
    if _should_update_title(existing, new_title, force, new_confidence):
        updates["title"] = new_title

    new_company = normalize_company_name(str(extracted.get("company") or ""))
    if new_company and (_is_unknown_company(existing.company) or force):
        updates["company"] = new_company

    new_location = str(extracted.get("location") or "").strip()
    if new_location and (not (existing.location or "").strip() or force):
        updates["location"] = new_location

    new_contract = str(extracted.get("contract") or "").strip()
    if new_contract and (not (existing.contract or "").strip() or force):
        updates["contract"] = new_contract

    new_salary_min = extracted.get("salary_min")
    new_salary_max = extracted.get("salary_max")
    new_salary_raw = str(extracted.get("salary_raw_text") or "").strip()

    old_salary_missing = existing.salary_min is None and existing.salary_max is None
    if (new_salary_min is not None or new_salary_max is not None) and (old_salary_missing or force):
        if new_salary_min is not None:
            updates["salary_min"] = float(new_salary_min)
        if new_salary_max is not None:
            updates["salary_max"] = float(new_salary_max)
        updates["salary_confidence"] = max(float(existing.salary_confidence or 0.0), new_confidence or 0.4)
        if new_salary_raw:
            updates["salary_raw_text"] = new_salary_raw
    elif new_salary_raw and not (existing.salary_raw_text or "").strip():
        updates["salary_raw_text"] = new_salary_raw

    new_techs = normalize_technologies(extracted.get("technologies") or [])
    if new_techs:
        existing_techs = normalize_technologies(existing.technologies)
        merged = normalize_technologies(existing_techs + new_techs)
        if merged != existing_techs:
            updates["technologies"] = merged

    new_description = _trim_noise(str(extracted.get("description") or ""))
    old_description = _trim_noise(existing.description)
    if new_description:
        if not old_description:
            updates["description"] = new_description
        elif force and len(new_description) >= 120:
            updates["description"] = new_description
        elif len(old_description) < 300 and len(new_description) > len(old_description):
            updates["description"] = new_description

    if new_confidence > float(existing.extraction_confidence or 0.0):
        updates["extraction_confidence"] = new_confidence
    new_source = str(extracted.get("extraction_source") or "").strip()
    if new_source:
        updates["extraction_source"] = new_source

    if updates:
        merged_offer = existing.model_copy(update=updates)
    else:
        merged_offer = existing

    for field, after in updates.items():
        before = getattr(existing, field)
        if before != after:
            fields_updated.append(field)
            merge_diff[field] = {"before": before, "after": after}

    return _MergeOutcome(offer=merged_offer, fields_updated=fields_updated, merge_diff=merge_diff)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)


def _persist_debug_artifacts(
    *,
    run_dir: Path,
    offer: Offer,
    extractor_used: str,
    modal_summary: dict[str, Any] | None,
    blocked: bool,
    blocked_reason: str,
    extracted_raw: dict[str, Any],
    normalized_payload: dict[str, Any],
    merge_diff: dict[str, Any],
    fields_updated: list[str],
    warnings: list[str],
    error: str,
) -> None:
    meta = {
        "timestamp": datetime.now(UTC).isoformat(),
        "source": offer.source,
        "url": offer.url,
        "extractor_used": extractor_used,
        "modal_actions": ((modal_summary or {}).get("actions_taken") or []),
        "blocked": blocked,
        "blocked_reason": blocked_reason,
        "captcha_detected": bool((modal_summary or {}).get("blocked_by_captcha")),
        "login_detected": bool((modal_summary or {}).get("blocked_by_login")),
        "fields_updated": fields_updated,
        "warnings": warnings,
        "error": error,
    }
    _write_json(run_dir / "metadata.json", meta)
    _write_json(run_dir / "extracted_raw.json", extracted_raw)
    _write_json(run_dir / "normalized.json", normalized_payload)
    _write_json(run_dir / "merge_diff.json", merge_diff)


def _rescore_offer(offer: Offer) -> Offer:
    settings = load_settings()
    llm_result: dict | None = None
    if settings.lm_studio.enabled:
        try:
            llm_result = get_llm_score(
                offer_data=offer.model_dump(mode="json"),
                criteria_data=settings.model_dump(mode="json"),
                config=settings.lm_studio,
            )
        except Exception:  # noqa: BLE001
            llm_result = None
    return score_offer(offer, settings, llm_result=llm_result)


def _deep_extract_offer_details(
    *,
    offer_id: str,
    force: bool,
    use_playwright: bool,
    run_id: str,
) -> DeepExtractionResult:
    offer = get_offer_by_id(offer_id)
    if offer is None:
        return DeepExtractionResult(
            offer_id=offer_id,
            status=DeepExtractionStatus.FAILED,
            error=f"Offer '{offer_id}' not found.",
        )

    completeness_before = is_offer_incomplete(offer)
    if not force and not completeness_before.is_incomplete:
        return DeepExtractionResult(
            offer_id=offer.id,
            url=offer.url,
            source=offer.source,
            status=DeepExtractionStatus.SKIPPED_COMPLETE,
            fields_before=_offer_field_snapshot(offer),
            fields_after=_offer_field_snapshot(offer),
            missing_fields_remaining=completeness_before.missing_fields,
            warnings=list(completeness_before.reasons),
        )

    run_dir = Path("data") / "debug" / "deep_extraction" / run_id / offer.id
    run_dir.mkdir(parents=True, exist_ok=True)

    payload = _extract_with_playwright(offer, run_dir) if use_playwright else _extract_without_playwright(offer)

    if payload.blocked:
        warnings = ["Page blocked; extraction was not bypassed."]
        _persist_debug_artifacts(
            run_dir=run_dir,
            offer=offer,
            extractor_used="blocked",
            modal_summary=payload.modal_summary,
            blocked=True,
            blocked_reason=payload.blocked_reason,
            extracted_raw={},
            normalized_payload={},
            merge_diff={},
            fields_updated=[],
            warnings=warnings,
            error="",
        )
        return DeepExtractionResult(
            offer_id=offer.id,
            url=offer.url,
            source=offer.source,
            status=DeepExtractionStatus.BLOCKED,
            fields_before=_offer_field_snapshot(offer),
            fields_after=_offer_field_snapshot(offer),
            missing_fields_remaining=completeness_before.missing_fields,
            extractor_used="blocked",
            warnings=warnings,
        )

    try:
        extracted_raw, extractor_used, warnings = _extract_raw_fields(offer, payload)
        merge_outcome = _merge_offer(offer, extracted_raw, force=force)
        merged_offer = merge_outcome.offer

        if not merge_outcome.fields_updated:
            completeness_after = is_offer_incomplete(merged_offer)
            _persist_debug_artifacts(
                run_dir=run_dir,
                offer=offer,
                extractor_used=extractor_used,
                modal_summary=payload.modal_summary,
                blocked=False,
                blocked_reason="",
                extracted_raw=extracted_raw,
                normalized_payload=_offer_field_snapshot(merged_offer),
                merge_diff=merge_outcome.merge_diff,
                fields_updated=[],
                warnings=warnings,
                error="",
            )
            return DeepExtractionResult(
                offer_id=offer.id,
                url=offer.url,
                source=offer.source,
                status=DeepExtractionStatus.NO_CHANGE,
                fields_before=_offer_field_snapshot(offer),
                fields_after=_offer_field_snapshot(merged_offer),
                fields_updated=[],
                missing_fields_remaining=completeness_after.missing_fields,
                extractor_used=extractor_used,
                warnings=warnings,
            )

        rescored_offer = _rescore_offer(merged_offer)
        update_offer(rescored_offer)
        sync_all_queue_items_from_offers()

        completeness_after = is_offer_incomplete(rescored_offer)

        _persist_debug_artifacts(
            run_dir=run_dir,
            offer=offer,
            extractor_used=extractor_used,
            modal_summary=payload.modal_summary,
            blocked=False,
            blocked_reason="",
            extracted_raw=extracted_raw,
            normalized_payload=_offer_field_snapshot(rescored_offer),
            merge_diff=merge_outcome.merge_diff,
            fields_updated=merge_outcome.fields_updated,
            warnings=warnings,
            error="",
        )

        return DeepExtractionResult(
            offer_id=offer.id,
            url=offer.url,
            source=offer.source,
            status=DeepExtractionStatus.UPDATED,
            fields_before=_offer_field_snapshot(offer),
            fields_after=_offer_field_snapshot(rescored_offer),
            fields_updated=merge_outcome.fields_updated,
            missing_fields_remaining=completeness_after.missing_fields,
            extractor_used=extractor_used,
            warnings=warnings,
        )
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
        logger.exception("Deep extraction failed for %s", offer.id)
        _persist_debug_artifacts(
            run_dir=run_dir,
            offer=offer,
            extractor_used=payload.extractor_used or "unknown",
            modal_summary=payload.modal_summary,
            blocked=False,
            blocked_reason="",
            extracted_raw={},
            normalized_payload={},
            merge_diff={},
            fields_updated=[],
            warnings=[],
            error=err,
        )
        return DeepExtractionResult(
            offer_id=offer.id,
            url=offer.url,
            source=offer.source,
            status=DeepExtractionStatus.FAILED,
            fields_before=_offer_field_snapshot(offer),
            fields_after=_offer_field_snapshot(offer),
            extractor_used=payload.extractor_used or "unknown",
            error=err,
        )


def deep_extract_offer_details(
    offer_id: str,
    force: bool = False,
    use_playwright: bool = True,
) -> DeepExtractionResult:
    """Deep extract details for a single offer.

    This enriches weak/missing fields, then re-scores and syncs queue snapshots.
    """

    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    return _deep_extract_offer_details(
        offer_id=offer_id,
        force=force,
        use_playwright=use_playwright,
        run_id=run_id,
    )


def deep_extract_offers(
    offer_ids: list[str],
    force: bool = False,
    only_incomplete: bool = True,
    max_offers: int | None = None,
) -> DeepExtractionBatchResult:
    """Deep extract details for multiple offers."""

    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]

    selected_ids = [oid for oid in offer_ids if oid]
    if max_offers is not None:
        selected_ids = selected_ids[: max(0, int(max_offers))]

    results: list[DeepExtractionResult] = []

    for offer_id in selected_ids:
        offer = get_offer_by_id(offer_id)
        if offer is None:
            results.append(
                DeepExtractionResult(
                    offer_id=offer_id,
                    status=DeepExtractionStatus.FAILED,
                    error=f"Offer '{offer_id}' not found.",
                )
            )
            continue

        if only_incomplete and not force:
            completeness = is_offer_incomplete(offer)
            if not completeness.is_incomplete:
                results.append(
                    DeepExtractionResult(
                        offer_id=offer.id,
                        url=offer.url,
                        source=offer.source,
                        status=DeepExtractionStatus.SKIPPED_COMPLETE,
                        fields_before=_offer_field_snapshot(offer),
                        fields_after=_offer_field_snapshot(offer),
                        missing_fields_remaining=completeness.missing_fields,
                        warnings=list(completeness.reasons),
                    )
                )
                continue

        results.append(
            _deep_extract_offer_details(
                offer_id=offer_id,
                force=force,
                use_playwright=True,
                run_id=run_id,
            )
        )

    return DeepExtractionBatchResult(results=results)
