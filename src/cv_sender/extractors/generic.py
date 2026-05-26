"""Generic offer extractor – works on any job board as a fallback.

Strategy:
1. Try JSON-LD ``JobPosting``
2. Try ``__NEXT_DATA__`` (generic traversal – best-effort)
3. Extract title from ``<title>`` tag

Does not make any HTTP requests.
"""

from __future__ import annotations

from typing import Any

from cv_sender.extractors.base import (
    DOM,
    EMBEDDED_STATE,
    GENERIC,
    JSON_LD,
    BaseExtractor,
    OfferDraft,
    clean_description,
    draft_from_json_ld,
    normalize_contract,
    normalize_currency,
    normalize_salary,
    normalize_technologies,
    parse_json_ld_jobposting,
    parse_next_data,
    parse_page_title,
)


class GenericExtractor(BaseExtractor):
    """Fallback extractor that tries structured data from any site."""

    source = GENERIC

    def can_handle(self, url: str) -> bool:  # noqa: ARG002
        return True  # always handles as last resort

    def extract(self, url: str, html: str) -> OfferDraft:  # noqa: ARG002
        # 1. JSON-LD JobPosting
        ld = parse_json_ld_jobposting(html)
        if ld:
            draft = draft_from_json_ld(ld)
            draft.extraction_source = JSON_LD
            return draft

        # 2. __NEXT_DATA__ generic traversal
        next_data = parse_next_data(html)
        if next_data:
            draft = _try_next_data_generic(next_data)
            if draft.title:
                draft.extraction_source = EMBEDDED_STATE
                return draft

        # 3. <title> tag only
        draft = OfferDraft()
        title = parse_page_title(html)
        if title:
            # Trim site name suffix like " – ACME Jobs" or " | JobBoard"
            for sep in [" – ", " — ", " | ", " - "]:
                if sep in title:
                    title = title.split(sep)[0].strip()
                    break
            draft.title = title
            draft.extraction_source = DOM
            draft.extraction_confidence = 0.1
        return draft


def _try_next_data_generic(data: dict[str, Any]) -> OfferDraft:
    """Best-effort extraction from any __NEXT_DATA__ structure."""
    draft = OfferDraft()
    # Descend into props.pageProps looking for an object that has a "title" field
    page_props = (data.get("props") or {}).get("pageProps") or {}
    _fill_from_dict(draft, page_props)
    if not draft.title:
        # Try one level deeper
        for value in page_props.values():
            if isinstance(value, dict) and value.get("title"):
                _fill_from_dict(draft, value)
                break
    draft.extraction_confidence = draft._filled_count() / 5
    return draft


def _fill_from_dict(draft: OfferDraft, d: dict[str, Any]) -> None:
    """Opportunistically fill draft fields from a dict with common key names."""
    if not isinstance(d, dict):
        return
    draft.title = str(d.get("title") or d.get("jobTitle") or d.get("name") or "").strip()
    draft.company = str(
        _nested(d, "company", "name")
        or _nested(d, "employer", "name")
        or d.get("companyName")
        or d.get("company")
        or ""
    ).strip()
    draft.location = str(
        d.get("city") or d.get("location") or d.get("address") or ""
    ).strip()
    draft.description = clean_description(
        str(d.get("description") or d.get("body") or d.get("content") or "")
    )
    # Salary
    sal_min = d.get("salaryFrom") or d.get("minimalSalary") or d.get("salary_min")
    sal_max = d.get("salaryTo") or d.get("maximalSalary") or d.get("salary_max")
    draft.salary_min = normalize_salary(sal_min)
    draft.salary_max = normalize_salary(sal_max)
    currency_raw = d.get("currency") or d.get("salaryCurrency") or "PLN"
    draft.currency = normalize_currency(currency_raw)
    # Contract
    draft.contract = normalize_contract(
        d.get("employmentType") or d.get("contract") or d.get("contractType") or ""
    )
    # Technologies / skills
    draft.technologies = normalize_technologies(
        d.get("skills") or d.get("technologies") or d.get("requiredSkills") or []
    )


def _nested(d: dict[str, Any], *keys: str) -> Any:
    """Safely traverse nested dicts: ``_nested(d, "a", "b")`` → ``d["a"]["b"]``."""
    current: Any = d
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
