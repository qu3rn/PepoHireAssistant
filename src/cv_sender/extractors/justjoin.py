"""Extractor for justjoin.it – uses Next.js ``__NEXT_DATA__``."""

from __future__ import annotations

from typing import Any

from cv_sender.extractors.base import (
    EMBEDDED_STATE,
    BaseExtractor,
    OfferDraft,
    clean_description,
    normalize_contract,
    normalize_currency,
    normalize_salary,
    normalize_technologies,
    parse_json_ld_jobposting,
    parse_next_data,
    draft_from_json_ld,
)

_HOSTNAMES = {"justjoin.it", "www.justjoin.it"}


class JustJoinExtractor(BaseExtractor):
    source = "justjoin"

    def can_handle(self, url: str) -> bool:
        from urllib.parse import urlparse  # noqa: PLC0415

        return urlparse(url).hostname in _HOSTNAMES

    def extract(self, url: str, html: str) -> OfferDraft:  # noqa: ARG002
        # 1. JSON-LD first
        ld = parse_json_ld_jobposting(html)
        if ld:
            draft = draft_from_json_ld(ld)
            if draft.title:
                return draft

        # 2. __NEXT_DATA__
        data = parse_next_data(html)
        if data:
            draft = _extract_from_next_data(data)
            if draft.title:
                return draft

        return OfferDraft()


def _extract_from_next_data(data: dict[str, Any]) -> OfferDraft:
    page_props = (data.get("props") or {}).get("pageProps") or {}

    # JustJoinIT uses "offer"; sometimes also "job" or "jobOffer"
    offer = (
        page_props.get("offer")
        or page_props.get("job")
        or page_props.get("jobOffer")
        or {}
    )
    if not isinstance(offer, dict) or not offer.get("title"):
        return OfferDraft()

    draft = OfferDraft()
    draft.extraction_source = EMBEDDED_STATE

    draft.title = str(offer.get("title") or "").strip()
    draft.company = str(
        offer.get("companyName") or offer.get("company_name") or offer.get("company") or ""
    ).strip()
    draft.location = str(
        offer.get("city") or offer.get("location") or offer.get("remote_interview") or ""
    ).strip()

    # Salary: JustJoin returns a list of salary objects [{from, to, currency, type}]
    salaries = offer.get("salary") or offer.get("salaries") or []
    if isinstance(salaries, list) and salaries:
        # Prefer B2B salary; fall back to first entry
        chosen = next((s for s in salaries if "b2b" in str(s.get("type", "")).lower()), salaries[0])
        if isinstance(chosen, dict):
            draft.salary_min = normalize_salary(chosen.get("from") or chosen.get("salary_from"))
            draft.salary_max = normalize_salary(chosen.get("to") or chosen.get("salary_to"))
            draft.currency = normalize_currency(chosen.get("currency"))
            draft.contract = normalize_contract(chosen.get("type") or "")
    elif isinstance(salaries, dict):
        draft.salary_min = normalize_salary(salaries.get("from"))
        draft.salary_max = normalize_salary(salaries.get("to"))
        draft.currency = normalize_currency(salaries.get("currency"))

    # Override contract if explicitly set on the offer
    if offer.get("employmentType") or offer.get("employment_type"):
        draft.contract = normalize_contract(
            offer.get("employmentType") or offer.get("employment_type")
        )

    # Work mode
    workplace = offer.get("workplaceType") or offer.get("workplace_type") or ""
    if workplace and workplace.lower() == "remote":
        if draft.location:
            draft.location = f"{draft.location} / remote"
        else:
            draft.location = "remote"

    # Skills
    skills_raw = offer.get("skills") or offer.get("requiredSkills") or []
    draft.technologies = normalize_technologies(skills_raw)

    # Description
    draft.description = clean_description(
        str(offer.get("body") or offer.get("description") or offer.get("content") or "")
    )

    draft.extraction_confidence = draft._filled_count() / 5
    if draft.extraction_confidence < 0.3:
        draft.extraction_warnings.append(
            "Low extraction confidence from JustJoinIT extractor."
        )
    return draft
