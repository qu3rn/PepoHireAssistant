"""Extractor registry, HTTP fetch helper, and public API.

Typical usage in the service layer::

    from cv_sender.extractors import extract_offer
    draft = extract_offer("https://rocketjobs.pl/oferty/frontend-123")

The :func:`_fetch_html` function can be replaced in tests via monkeypatching::

    monkeypatch.setattr(cv_sender.extractors, "_fetch_html", lambda url: None)
"""

from __future__ import annotations

import logging

from cv_sender.extractors.base import BaseExtractor, OfferDraft
from cv_sender.extractors.generic import GenericExtractor
from cv_sender.extractors.justjoin import JustJoinExtractor
from cv_sender.extractors.nofluffjobs import NoFluffJobsExtractor
from cv_sender.extractors.pracuj import PracujExtractor
from cv_sender.extractors.rocketjobs import RocketJobsExtractor

logger = logging.getLogger("cv_sender.extractors")

# ---------------------------------------------------------------------------
# Extractor registry – order matters: specific first, generic last
# ---------------------------------------------------------------------------

_REGISTRY: list[BaseExtractor] = [
    RocketJobsExtractor(),
    JustJoinExtractor(),
    NoFluffJobsExtractor(),
    PracujExtractor(),
    GenericExtractor(),
]


def get_extractor(url: str) -> BaseExtractor:
    """Return the best :class:`BaseExtractor` for *url*."""
    for extractor in _REGISTRY:
        if extractor.can_handle(url):
            return extractor
    return GenericExtractor()


# ---------------------------------------------------------------------------
# HTTP fetch (module-level so tests can monkeypatch it)
# ---------------------------------------------------------------------------

_FETCH_TIMEOUT = 10.0
_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl,en-US;q=0.9,en;q=0.8",
}


def _fetch_html(url: str) -> str | None:
    """Fetch HTML from *url*.  Returns ``None`` on any error (never raises).

    Tests replace this with a monkeypatch that returns ``None`` or fixture HTML.
    """
    try:
        import httpx  # noqa: PLC0415

        with httpx.Client(
            timeout=_FETCH_TIMEOUT,
            follow_redirects=True,
            max_redirects=3,
        ) as client:
            resp = client.get(url, headers=_FETCH_HEADERS)

        if resp.status_code == 200:
            return resp.text

        logger.info("HTTP %d fetching %s – extraction will use URL-only mode", resp.status_code, url)
        return None
    except ImportError:
        logger.warning("httpx not installed – HTML fetch skipped")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("Fetch failed for %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_offer(url: str) -> OfferDraft:
    """Fetch HTML and extract offer data using the best matching extractor.

    Falls back through the extractor chain.  If fetching fails or the
    source-specific extractor has low confidence, the generic extractor is
    tried as well.  **Never raises** – returns an empty :class:`OfferDraft`
    on complete failure.
    """
    html = _fetch_html(url)
    if not html:
        return OfferDraft()

    extractor = get_extractor(url)

    try:
        draft = extractor.extract(url, html)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Extractor %r failed for %s: %s", extractor.source, url, exc)
        draft = OfferDraft()

    # If the specific extractor returned low confidence, try generic as fallback
    if draft.extraction_confidence < 0.3 and not isinstance(extractor, GenericExtractor):
        try:
            generic_draft = GenericExtractor().extract(url, html)
        except Exception:  # noqa: BLE001
            generic_draft = OfferDraft()

        if generic_draft.extraction_confidence >= draft.extraction_confidence:
            if draft.extraction_confidence > 0:
                generic_draft.extraction_warnings.append(
                    f"Fell back to generic extractor (source-specific confidence: "
                    f"{draft.extraction_confidence:.0%})."
                )
            return generic_draft

    return draft
