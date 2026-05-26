"""Extractor for rocketjobs.pl – uses Next.js ``__NEXT_DATA__``."""

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

_HOSTNAMES = {"rocketjobs.pl", "www.rocketjobs.pl"}


class RocketJobsExtractor(BaseExtractor):
    source = "rocketjobs"

    def can_handle(self, url: str) -> bool:
        from urllib.parse import urlparse  # noqa: PLC0415

        return urlparse(url).hostname in _HOSTNAMES

    def extract(self, url: str, html: str) -> OfferDraft:  # noqa: ARG002
        # 1. Try JSON-LD first (most reliable if present)
        ld = parse_json_ld_jobposting(html)
        if ld:
            draft = draft_from_json_ld(ld)
            if draft.title:
                return draft

        # 2. __NEXT_DATA__ with RocketJobs-specific field mapping
        data = parse_next_data(html)
        if data:
            draft = _extract_from_next_data(data)
            if draft.title:
                return draft

        return OfferDraft()


def _extract_from_next_data(data: dict[str, Any]) -> OfferDraft:
    page_props = (data.get("props") or {}).get("pageProps") or {}

    # RocketJobs uses "jobOffer" key; sometimes "offer"
    offer = (
        page_props.get("jobOffer")
        or page_props.get("offer")
        or page_props.get("job")
        or {}
    )
    if not isinstance(offer, dict) or not offer.get("title"):
        return OfferDraft()

    draft = OfferDraft()
    draft.extraction_source = EMBEDDED_STATE

    draft.title = str(offer.get("title") or "").strip()

    # Company
    employer = offer.get("employer") or offer.get("company") or {}
    if isinstance(employer, dict):
        draft.company = str(employer.get("name") or "").strip()
    elif isinstance(employer, str):
        draft.company = employer.strip()

    # Location
    draft.location = str(
        offer.get("city") or offer.get("location") or offer.get("cityName") or ""
    ).strip()

    # Salary
    draft.salary_min = normalize_salary(
        offer.get("minimalSalary") or offer.get("salaryFrom") or offer.get("salary_min")
    )
    draft.salary_max = normalize_salary(
        offer.get("maximalSalary") or offer.get("salaryTo") or offer.get("salary_max")
    )
    draft.currency = normalize_currency(offer.get("currency") or offer.get("salaryCurrency"))

    # Contract type
    emp_type = offer.get("employmentType") or offer.get("employmentTypes") or ""
    if isinstance(emp_type, list):
        emp_type = emp_type[0].get("name", "") if emp_type and isinstance(emp_type[0], dict) else (emp_type[0] if emp_type else "")
    elif isinstance(emp_type, dict):
        emp_type = emp_type.get("name", "")
    draft.contract = normalize_contract(str(emp_type))

    # Technologies / skills
    required = offer.get("requiredSkills") or offer.get("skills") or []
    nice = offer.get("niceToHave") or offer.get("optionalSkills") or []
    draft.technologies = normalize_technologies(required + nice)

    # Description
    draft.description = clean_description(
        str(offer.get("description") or offer.get("body") or "")
    )

    draft.extraction_confidence = draft._filled_count() / 5
    if draft.extraction_confidence < 0.3:
        draft.extraction_warnings.append(
            "Low extraction confidence from RocketJobs extractor."
        )
    return draft
