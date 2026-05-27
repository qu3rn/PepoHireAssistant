"""NoFluffJobs job collector — uses the public REST API."""

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

_API_BASE = "https://nofluffjobs.com/api/search/posting"


class NoFluffJobsCollector(BaseJobCollector):
    """Collector for nofluffjobs.com using the public JSON API."""

    source = "nofluffjobs"

    def search(self, criteria: JobSearchCriteria) -> list[CollectedOffer]:
        results: list[CollectedOffer] = []
        seen_urls: set[str] = set()

        for keyword in criteria.keywords:
            if len(results) >= criteria.max_offers_per_source:
                break

            page = 1
            while len(results) < criteria.max_offers_per_source:
                payload = _build_payload(keyword, criteria, page)
                data = _fetch_json_post(_API_BASE, payload)
                if data is None:
                    break

                postings = data.get("postings") or data.get("data") or []
                if not postings:
                    break

                for item in postings:
                    if len(results) >= criteria.max_offers_per_source:
                        break
                    offer = _parse_item(item)
                    if offer and offer.url not in seen_urls:
                        seen_urls.add(offer.url)
                        results.append(offer)

                total_pages = data.get("totalPages") or data.get("total_pages") or 1
                if page >= total_pages:
                    break
                page += 1
                _sleep(criteria.request_delay_seconds)

        return results[: criteria.max_offers_per_source]


def _build_payload(keyword: str, criteria: JobSearchCriteria, page: int) -> dict:
    payload: dict = {
        "criteria": {
            "requirement": [{"value": keyword, "type": "keyword"}],
        },
        "page": page,
        "pageSize": 20,
        "language": "en",
    }
    if criteria.locations:
        remote_loc = any(loc.lower() == "remote" for loc in criteria.locations)
        if remote_loc:
            payload["criteria"]["remote"] = True
    return payload


def _fetch_json_post(url: str, payload: dict):
    try:
        import requests  # noqa: PLC0415

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 cv-sender/1.0",
            "Accept": "application/json",
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("POST request failed for %s: %s", url, exc)
        return None


def _parse_item(item: dict) -> CollectedOffer | None:
    offer_id = item.get("id") or item.get("url") or ""
    if not offer_id:
        return None

    # URL
    slug = item.get("url") or offer_id
    url = f"https://nofluffjobs.com/pl/job/{slug}" if not slug.startswith("http") else slug

    salary_min: float | None = None
    salary_max: float | None = None
    currency = "PLN"
    contract = ""

    salary = item.get("salary") or {}
    if isinstance(salary, dict):
        salary_min = salary.get("from")
        salary_max = salary.get("to")
        currency = salary.get("currency", "PLN").upper()
        contract = salary.get("type") or ""

    requirements = item.get("technology") or item.get("requirements") or {}
    skills_raw = requirements.get("must", []) + requirements.get("nice", []) if isinstance(requirements, dict) else []
    technologies = [s if isinstance(s, str) else s.get("value", "") for s in skills_raw]

    location_raw = item.get("location") or {}
    places = location_raw.get("places") or location_raw.get("cities") or []
    location = places[0].get("city") if places and isinstance(places[0], dict) else str(places[0]) if places else ""
    if location_raw.get("fullyRemote"):
        location = "Remote" if not location else f"Remote / {location}"

    return CollectedOffer(
        source="nofluffjobs",
        url=url,
        title=item.get("title") or item.get("position") or "",
        company=item.get("name") or item.get("company") or item.get("companyName") or "",
        location=location,
        salary_min=float(salary_min) if salary_min is not None else None,
        salary_max=float(salary_max) if salary_max is not None else None,
        currency=currency,
        contract=contract,
        technologies=technologies,
        description_preview=item.get("content") or item.get("description") or "",
        raw_data=item,
    )
