"""Pracuj.pl job collector — uses the public listing API."""

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

_API_BASE = "https://massachusetts.pracuj.pl/jobs"


class PracujCollector(BaseJobCollector):
    """Collector for pracuj.pl using the public JSON API."""

    source = "pracuj"

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
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "Accept": "application/json",
                    "Accept-Language": "pl-PL,pl;q=0.9",
                }
                data = _fetch_json(url, headers=headers)
                if data is None:
                    break

                offers_raw = (
                    data.get("groupedOffers")
                    or data.get("offers")
                    or data.get("data")
                    or []
                )
                if not offers_raw:
                    break

                for item in offers_raw:
                    if len(results) >= criteria.max_offers_per_source:
                        break
                    # groupedOffers may contain nested "offers" list
                    sub_offers = item.get("offers") if isinstance(item, dict) else None
                    if sub_offers and isinstance(sub_offers, list):
                        for sub in sub_offers:
                            if len(results) >= criteria.max_offers_per_source:
                                break
                            offer = _parse_item(sub)
                            if offer and offer.url not in seen_urls:
                                seen_urls.add(offer.url)
                                results.append(offer)
                    else:
                        offer = _parse_item(item)
                        if offer and offer.url not in seen_urls:
                            seen_urls.add(offer.url)
                            results.append(offer)

                total_count = data.get("totalCount") or 0 if isinstance(data, dict) else 0
                page_size = data.get("pageSize") or 20 if isinstance(data, dict) else 20
                if total_count and page * page_size >= total_count:
                    break
                page += 1
                _sleep(criteria.request_delay_seconds)

        return results[: criteria.max_offers_per_source]


def _build_params(keyword: str, criteria: JobSearchCriteria, page: int) -> dict[str, str]:
    params: dict[str, str] = {
        "q": keyword,
        "pn": str(page),
    }
    if criteria.locations:
        remote = any(loc.lower() == "remote" for loc in criteria.locations)
        if remote:
            params["wm"] = "4"  # pracuj.pl "fully remote" filter code
    return params


def _parse_item(item: dict) -> CollectedOffer | None:
    offer_id = item.get("id") or item.get("offerId") or ""
    if not offer_id:
        return None

    # Build canonical URL
    slug = item.get("offerAbsoluteUri") or item.get("canonicalURL") or ""
    url = slug if slug.startswith("http") else f"https://www.pracuj.pl/praca/{offer_id}"

    salary_min: float | None = None
    salary_max: float | None = None
    currency = "PLN"
    contract = ""

    salary_raw = item.get("salaryDisplayText") or ""
    salary_ranges = item.get("salaryRanges") or item.get("salary") or []
    if isinstance(salary_ranges, list) and salary_ranges:
        first = salary_ranges[0]
        salary_min = first.get("from")
        salary_max = first.get("to")
        currency = first.get("currency", "PLN").upper()
        contract = first.get("contractType") or ""
    elif isinstance(salary_ranges, dict):
        salary_min = salary_ranges.get("from")
        salary_max = salary_ranges.get("to")
        currency = salary_ranges.get("currency", "PLN").upper()
        contract = salary_ranges.get("contractType") or ""

    tags = item.get("tags") or item.get("technologies") or []
    technologies = [t if isinstance(t, str) else t.get("name", "") for t in tags]

    location = item.get("workplaceAddress") or item.get("city") or ""

    return CollectedOffer(
        source="pracuj",
        url=url,
        title=item.get("jobTitle") or item.get("position") or item.get("title") or "",
        company=item.get("companyName") or item.get("employer") or "",
        location=location,
        salary_min=float(salary_min) if salary_min is not None else None,
        salary_max=float(salary_max) if salary_max is not None else None,
        currency=currency,
        contract=contract,
        technologies=technologies,
        description_preview=(item.get("jobDescription") or "")[:300],
        raw_data=item,
    )
