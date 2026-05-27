"""NoFluffJobs job collector — uses the public listing API.

The ``/api/search/posting`` POST endpoint requires an undocumented ``salaryCurrency``
parameter and is effectively broken for unauthenticated requests (400).

The ``/api/posting`` GET endpoint works without auth but returns the full dataset
(~20 000 postings) regardless of keyword/pageSize params — pagination is ignored
server-side.  We work around this by streaming the response and stopping after
reading at most ``_MAX_FETCH_BYTES`` bytes, then filtering locally.
"""

from __future__ import annotations

import json
import logging

from cv_sender.collectors.base import (
    BaseJobCollector,
    CollectedOffer,
    JobSearchCriteria,
)

logger = logging.getLogger(__name__)

# The only publicly accessible unauthenticated endpoint (GET, returns full dataset).
# The POST /api/search/posting endpoint is broken — it always returns HTTP 400
# ("Required parameter 'salaryCurrency' is not present") regardless of the payload.
_API_BASE = "https://nofluffjobs.com/api/posting"

# Limit how much of the (~150 MB) full response we download per run.
# At ~7–8 KB per item this gives us ~1 300–2 000 raw candidates to filter from.
# The endpoint returns offers in descending date order so recent postings come first.
_MAX_FETCH_BYTES = 12 * 1024 * 1024  # 12 MB

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9,pl;q=0.8",
}


class NoFluffJobsCollector(BaseJobCollector):
    """Collector for nofluffjobs.com.

    Fetches the public listing feed (GET /api/posting), streams up to
    ``_MAX_FETCH_BYTES`` of the response body, then filters locally by the
    caller's keyword / technology criteria.
    """

    source = "nofluffjobs"

    def search(self, criteria: JobSearchCriteria) -> list[CollectedOffer]:
        try:
            import requests  # noqa: PLC0415
        except ImportError:
            logger.error("requests library not installed")
            return []

        results: list[CollectedOffer] = []
        seen_urls: set[str] = set()

        logger.debug("NoFluffJobs: fetching %s (streaming, max %d MB)", _API_BASE, _MAX_FETCH_BYTES // (1024 * 1024))

        try:
            resp = requests.get(_API_BASE, headers=_HEADERS, timeout=30, stream=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("NoFluffJobs: HTTP request failed: %s", exc)
            return []

        if resp.status_code != 200:
            logger.warning(
                "NoFluffJobs: unexpected HTTP %d for %s", resp.status_code, _API_BASE
            )
            resp.close()
            return []

        # Read up to _MAX_FETCH_BYTES then close the connection early.
        chunks: list[bytes] = []
        total_bytes = 0
        try:
            for chunk in resp.iter_content(chunk_size=512 * 1024):
                chunks.append(chunk)
                total_bytes += len(chunk)
                if total_bytes >= _MAX_FETCH_BYTES:
                    logger.debug(
                        "NoFluffJobs: reached %d-byte read limit, closing stream", _MAX_FETCH_BYTES
                    )
                    break
        finally:
            resp.close()

        raw_text = b"".join(chunks).decode("utf-8", errors="replace")
        logger.debug("NoFluffJobs: read %d bytes, parsing partial JSON", total_bytes)

        items = _parse_postings_partial(raw_text)
        logger.debug("NoFluffJobs: parsed %d raw items from streamed response", len(items))

        for item in items:
            if len(results) >= criteria.max_offers_per_source:
                break
            offer = _parse_item(item)
            if offer is None or offer.url in seen_urls:
                continue
            seen_urls.add(offer.url)
            results.append(offer)

        return results


# ---------------------------------------------------------------------------
# Incremental JSON extraction
# ---------------------------------------------------------------------------

def _parse_postings_partial(raw: str) -> list[dict]:
    """Extract as many complete JSON posting objects as possible from *raw*.

    Handles truncated JSON gracefully — stops parsing when it hits incomplete
    data near the end of the buffer.
    """
    # Locate the start of the postings array.
    marker = '"postings":['
    start = raw.find(marker)
    if start < 0:
        logger.warning("NoFluffJobs: 'postings' key not found in response")
        return []

    pos = start + len(marker)
    decoder = json.JSONDecoder()
    items: list[dict] = []

    while pos < len(raw):
        # Skip whitespace and commas between items.
        while pos < len(raw) and raw[pos] in " \n\r\t,":
            pos += 1
        if pos >= len(raw) or raw[pos] == "]":
            break
        try:
            obj, end_pos = decoder.raw_decode(raw, pos)
            if isinstance(obj, dict):
                items.append(obj)
            pos = end_pos
        except json.JSONDecodeError:
            # Reached incomplete data (likely end of streamed buffer) — stop.
            break

    return items


# ---------------------------------------------------------------------------
# Item parser
# ---------------------------------------------------------------------------

def _parse_item(item: dict) -> CollectedOffer | None:
    offer_id = item.get("id") or item.get("url") or ""
    if not offer_id:
        return None

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

    # Technologies come from the "flavors" list (e.g. ["it"]) and "title" keywords.
    # NFJ doesn't expose a structured tech list in the listing feed; we use the
    # category "title" as a proxy so the local keyword/tech filter can match.
    flavors: list[str] = item.get("flavors") or []
    technologies = [f for f in flavors if isinstance(f, str)]

    location_raw = item.get("location") or {}
    places = location_raw.get("places") or []
    location = ""
    if places and isinstance(places[0], dict):
        location = places[0].get("city") or ""
    if item.get("fullyRemote"):
        location = "Remote" if not location else f"Remote / {location}"

    return CollectedOffer(
        source="nofluffjobs",
        url=url,
        title=item.get("title") or item.get("position") or "",
        company=item.get("name") or item.get("company") or "",
        location=location,
        salary_min=float(salary_min) if salary_min is not None else None,
        salary_max=float(salary_max) if salary_max is not None else None,
        currency=currency,
        contract=contract,
        technologies=technologies,
        description_preview="",  # listing feed does not include description
        raw_data=item,
    )


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
