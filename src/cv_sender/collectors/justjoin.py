"""JustJoinIT job collector — uses the public JSON API."""

from __future__ import annotations

import logging

from cv_sender.collectors.base import (
    BaseJobCollector,
    CollectedOffer,
    JobSearchCriteria,
    _fetch_json,
    _sleep,
)

logger = logging.getLogger(__name__)

# JustJoinIT public listing API (no auth required, paginated)
_API_BASE = "https://api.justjoin.it/v2/user-panel/offers"
_PAGE_SIZE = 50


class JustJoinCollector(BaseJobCollector):
    """Collector for justjoin.it using the public API."""

    source = "justjoin"

    def search(self, criteria: JobSearchCriteria) -> list[CollectedOffer]:
        results: list[CollectedOffer] = []
        seen_urls: set[str] = set()

        for keyword in criteria.keywords:
            if len(results) >= criteria.max_offers_per_source:
                break

            page = 1
            while len(results) < criteria.max_offers_per_source:
                params = _build_params(keyword, criteria, page)
                url = _API_BASE + "?" + "&".join(f"{k}={v}" for k, v in params.items())
                data = _fetch_json(url)
                if data is None:
                    break

                items = data if isinstance(data, list) else data.get("data", [])
                if not items:
                    break

                for item in items:
                    if len(results) >= criteria.max_offers_per_source:
                        break
                    offer = _parse_item(item)
                    if offer and offer.url not in seen_urls:
                        seen_urls.add(offer.url)
                        results.append(offer)

                # Check if there's a next page
                meta = data.get("meta", {}) if isinstance(data, dict) else {}
                total_pages = meta.get("total_pages", 1)
                if page >= total_pages:
                    break
                page += 1
                _sleep(criteria.request_delay_seconds)

        return results[: criteria.max_offers_per_source]


def _build_params(keyword: str, criteria: JobSearchCriteria, page: int) -> dict[str, str]:
    params: dict[str, str] = {
        "searchQuery": keyword.replace(" ", "+"),
        "page": str(page),
        "perPage": str(_PAGE_SIZE),
        "sortBy": "published",
        "orderBy": "DESC",
    }
    if criteria.locations:
        # "Remote" maps to remoteOnly flag
        if any(loc.lower() == "remote" for loc in criteria.locations):
            params["remoteOnly"] = "true"
    return params


def _parse_item(item: dict) -> CollectedOffer | None:
    slug = item.get("slug") or item.get("id") or ""
    if not slug:
        return None
    url = f"https://justjoin.it/offers/{slug}"

    salary_min: float | None = None
    salary_max: float | None = None
    currency = "PLN"
    contract = ""
    salary_ranges = item.get("salaryRanges") or item.get("multilocation") or []
    if salary_ranges and isinstance(salary_ranges, list):
        first = salary_ranges[0] if salary_ranges else {}
        salary_min = first.get("salaryFrom") or first.get("from") or None
        salary_max = first.get("salaryTo") or first.get("to") or None
        currency = first.get("currency", "PLN").upper()
        contract = first.get("employmentType") or item.get("workingTime") or ""

    skills_raw = item.get("requiredSkills") or item.get("skills") or []
    technologies = [s.get("name", s) if isinstance(s, dict) else str(s) for s in skills_raw]

    location = ""
    multiloc = item.get("multilocation") or []
    if multiloc and isinstance(multiloc, list):
        location = multiloc[0].get("city") or multiloc[0].get("slug") or ""
    if not location:
        location = item.get("city") or ""

    return CollectedOffer(
        source="justjoin",
        url=url,
        title=item.get("title") or "",
        company=item.get("companyName") or item.get("company_name") or "",
        location=location,
        salary_min=float(salary_min) if salary_min is not None else None,
        salary_max=float(salary_max) if salary_max is not None else None,
        currency=currency,
        contract=contract,
        technologies=technologies,
        description_preview=item.get("descriptionPreview") or "",
        raw_data=item,
    )
