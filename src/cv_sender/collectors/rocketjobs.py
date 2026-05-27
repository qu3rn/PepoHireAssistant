"""RocketJobs job collector — uses the public listing API."""

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

_API_BASE = "https://api.rocketjobs.pl/api/offers"


class RocketJobsCollector(BaseJobCollector):
    """Collector for rocketjobs.pl using the public JSON API."""

    source = "rocketjobs"

    def search(self, criteria: JobSearchCriteria) -> list[CollectedOffer]:
        results: list[CollectedOffer] = []
        seen_urls: set[str] = set()

        for keyword in criteria.keywords:
            if len(results) >= criteria.max_offers_per_source:
                break

            page = 0
            while len(results) < criteria.max_offers_per_source:
                params = _build_params(keyword, criteria, page)
                url = _API_BASE + "?" + "&".join(f"{k}={v}" for k, v in params.items())
                data = _fetch_json(url)
                if data is None:
                    break

                items = []
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    items = data.get("data", data.get("offers", data.get("results", [])))

                if not items:
                    break

                for item in items:
                    if len(results) >= criteria.max_offers_per_source:
                        break
                    offer = _parse_item(item)
                    if offer and offer.url not in seen_urls:
                        seen_urls.add(offer.url)
                        results.append(offer)

                # Paginate
                total = data.get("total", len(items)) if isinstance(data, dict) else len(items)
                if (page + 1) * 20 >= total:
                    break
                page += 1
                _sleep(criteria.request_delay_seconds)

        return results[: criteria.max_offers_per_source]


def _build_params(keyword: str, criteria: JobSearchCriteria, page: int) -> dict[str, str]:
    params: dict[str, str] = {
        "query": keyword,
        "page": str(page),
        "perPage": "20",
    }
    if criteria.locations:
        remote_loc = next((l for l in criteria.locations if l.lower() == "remote"), None)
        if remote_loc:
            params["remote"] = "true"
    return params


def _parse_item(item: dict) -> CollectedOffer | None:
    slug = item.get("slug") or item.get("id") or ""
    if not slug:
        return None
    url = f"https://rocketjobs.pl/oferty-pracy/{slug}"

    salary_min: float | None = None
    salary_max: float | None = None
    currency = "PLN"
    contract = ""

    salary = item.get("salary") or item.get("salaryRange") or {}
    if isinstance(salary, dict):
        salary_min = salary.get("from") or salary.get("min")
        salary_max = salary.get("to") or salary.get("max")
        currency = salary.get("currency", "PLN").upper()
        contract = salary.get("type") or item.get("employmentType") or ""
    elif isinstance(salary, list) and salary:
        first = salary[0]
        salary_min = first.get("from")
        salary_max = first.get("to")
        currency = first.get("currency", "PLN").upper()
        contract = first.get("type") or ""

    skills = item.get("skills") or item.get("technologies") or item.get("requiredSkills") or []
    technologies = [s.get("name", s) if isinstance(s, dict) else str(s) for s in skills]

    location = item.get("city") or item.get("location") or ""

    return CollectedOffer(
        source="rocketjobs",
        url=url,
        title=item.get("title") or item.get("position") or "",
        company=item.get("company") or item.get("companyName") or "",
        location=location,
        salary_min=float(salary_min) if salary_min is not None else None,
        salary_max=float(salary_max) if salary_max is not None else None,
        currency=currency,
        contract=contract,
        technologies=technologies,
        description_preview=item.get("descriptionShort") or item.get("preview") or "",
        raw_data=item,
    )
