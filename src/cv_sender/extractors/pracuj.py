"""Extractor for pracuj.pl – tries JSON-LD then ``__NEXT_DATA__``."""

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

_HOSTNAMES = {"pracuj.pl", "www.pracuj.pl"}


class PracujExtractor(BaseExtractor):
    source = "pracuj"

    def can_handle(self, url: str) -> bool:
        from urllib.parse import urlparse  # noqa: PLC0415

        return urlparse(url).hostname in _HOSTNAMES

    def extract(self, url: str, html: str) -> OfferDraft:  # noqa: ARG002
        # 1. JSON-LD (Pracuj.pl typically includes a well-formed JobPosting schema)
        ld = parse_json_ld_jobposting(html)
        if ld:
            draft = draft_from_json_ld(ld)
            if draft.title:
                return draft

        # 2. __NEXT_DATA__ fallback
        data = parse_next_data(html)
        if data:
            draft = _extract_from_next_data(data)
            if draft.title:
                return draft

        return OfferDraft()


def _extract_from_next_data(data: dict[str, Any]) -> OfferDraft:
    page_props = (data.get("props") or {}).get("pageProps") or {}

    # Pracuj may use "jobOffer", "offer", or similar
    offer = (
        page_props.get("jobOffer")
        or page_props.get("offer")
        or page_props.get("job")
        or {}
    )
    if not isinstance(offer, dict):
        return OfferDraft()

    title = str(offer.get("title") or offer.get("jobTitle") or "").strip()
    if not title:
        return OfferDraft()

    draft = OfferDraft()
    draft.extraction_source = EMBEDDED_STATE
    draft.title = title

    # Company
    employer = offer.get("employer") or offer.get("company") or offer.get("companyName") or {}
    if isinstance(employer, dict):
        draft.company = str(employer.get("name") or "").strip()
    elif isinstance(employer, str):
        draft.company = employer.strip()

    # Location
    locations = offer.get("locations") or offer.get("workplaces") or []
    if isinstance(locations, list) and locations:
        loc = locations[0]
        if isinstance(loc, dict):
            draft.location = str(loc.get("city") or loc.get("location") or "").strip()
        elif isinstance(loc, str):
            draft.location = loc.strip()
    else:
        draft.location = str(
            offer.get("city") or offer.get("location") or offer.get("workPlace") or ""
        ).strip()

    # Salary
    salary = offer.get("salary") or offer.get("salaryInfo") or {}
    if isinstance(salary, dict):
        draft.salary_min = normalize_salary(salary.get("from") or salary.get("min") or salary.get("salaryFrom"))
        draft.salary_max = normalize_salary(salary.get("to") or salary.get("max") or salary.get("salaryTo"))
        draft.currency = normalize_currency(salary.get("currency"))
        draft.contract = normalize_contract(salary.get("employmentType") or salary.get("type") or "")
    elif isinstance(salary, list) and salary:
        first = salary[0]
        if isinstance(first, dict):
            draft.salary_min = normalize_salary(first.get("from"))
            draft.salary_max = normalize_salary(first.get("to"))
            draft.currency = normalize_currency(first.get("currency"))

    # Contract override from top-level
    if offer.get("employmentType") or offer.get("contractType"):
        draft.contract = normalize_contract(
            offer.get("employmentType") or offer.get("contractType")
        )

    # Technologies / requirements
    techs_raw = (
        offer.get("technologies")
        or offer.get("skills")
        or offer.get("requirements")
        or []
    )
    draft.technologies = normalize_technologies(techs_raw)

    # Description
    draft.description = clean_description(
        str(offer.get("description") or offer.get("jobDescription") or offer.get("body") or "")
    )

    draft.extraction_confidence = draft._filled_count() / 5
    if draft.extraction_confidence < 0.3:
        draft.extraction_warnings.append(
            "Low extraction confidence from Pracuj extractor."
        )
    return draft
