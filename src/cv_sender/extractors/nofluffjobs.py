"""Extractor for nofluffjobs.com – tries JSON-LD then ``__NEXT_DATA__``."""

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

_HOSTNAMES = {"nofluffjobs.com", "www.nofluffjobs.com"}


class NoFluffJobsExtractor(BaseExtractor):
    source = "nofluffjobs"

    def can_handle(self, url: str) -> bool:
        from urllib.parse import urlparse  # noqa: PLC0415

        return urlparse(url).hostname in _HOSTNAMES

    def extract(self, url: str, html: str) -> OfferDraft:  # noqa: ARG002
        # 1. JSON-LD first (nofluffjobs often embeds a good JobPosting schema)
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

    # NoFluffJobs may use "post", "job", "offer"
    posting = (
        page_props.get("post")
        or page_props.get("job")
        or page_props.get("offer")
        or page_props.get("jobOffer")
        or {}
    )
    if not isinstance(posting, dict):
        return OfferDraft()

    # Basics may be a nested dict
    basics = posting.get("basics") or posting
    if not isinstance(basics, dict):
        basics = posting

    title = str(basics.get("title") or basics.get("name") or "").strip()
    if not title:
        return OfferDraft()

    draft = OfferDraft()
    draft.extraction_source = EMBEDDED_STATE
    draft.title = title

    # Company
    company_data = posting.get("company") or basics.get("company") or {}
    if isinstance(company_data, dict):
        draft.company = str(company_data.get("name") or "").strip()
    elif isinstance(company_data, str):
        draft.company = company_data.strip()

    # Location
    location_data = basics.get("location") or posting.get("location") or {}
    if isinstance(location_data, dict):
        draft.location = str(
            location_data.get("city")
            or location_data.get("name")
            or location_data.get("place")
            or ""
        ).strip()
    elif isinstance(location_data, str):
        draft.location = location_data.strip()

    # Salary
    salary_data = basics.get("salary") or posting.get("salary") or {}
    if isinstance(salary_data, dict):
        draft.salary_min = normalize_salary(salary_data.get("from") or salary_data.get("min"))
        draft.salary_max = normalize_salary(salary_data.get("to") or salary_data.get("max"))
        draft.currency = normalize_currency(salary_data.get("currency"))
        draft.contract = normalize_contract(salary_data.get("type") or "")

    # Contract override
    if basics.get("employmentType") or basics.get("employment_type"):
        draft.contract = normalize_contract(
            basics.get("employmentType") or basics.get("employment_type")
        )

    # Technologies
    specs = posting.get("specs") or posting
    requirements = specs.get("requirements") or specs.get("technologies") or []
    if isinstance(requirements, dict):
        # {"technologies": ["React", ...], "nice": [...]}
        required = requirements.get("technologies") or requirements.get("required") or []
        nice = requirements.get("nice") or requirements.get("optional") or []
        all_techs = (required if isinstance(required, list) else []) + \
                    (nice if isinstance(nice, list) else [])
    elif isinstance(requirements, list):
        all_techs = requirements
    else:
        all_techs = []
    draft.technologies = normalize_technologies(all_techs)

    # Description
    draft.description = clean_description(
        str(posting.get("description") or basics.get("description") or "")
    )

    draft.extraction_confidence = draft._filled_count() / 5
    if draft.extraction_confidence < 0.3:
        draft.extraction_warnings.append(
            "Low extraction confidence from NoFluffJobs extractor."
        )
    return draft
