"""Orchestrates browser-based form filling for a job application."""

from __future__ import annotations

from urllib.parse import urlparse

from cv_sender.config import Profile, Settings
from cv_sender.models import Offer
from cv_sender.portals.base import BasePortalFiller
from cv_sender.portals.generic import GenericFiller
from cv_sender.portals.linkedin import LinkedInFiller
from cv_sender.portals.pracuj import PracujFiller
from cv_sender.portals.rocketjobs import RocketJobsFiller

_PORTAL_MAP: dict[str, type[BasePortalFiller]] = {
    "rocketjobs.pl": RocketJobsFiller,
    "www.rocketjobs.pl": RocketJobsFiller,
    "pracuj.pl": PracujFiller,
    "www.pracuj.pl": PracujFiller,
    "linkedin.com": LinkedInFiller,
    "www.linkedin.com": LinkedInFiller,
}


def _choose_filler(url: str, profile: Profile, settings: Settings) -> BasePortalFiller:
    """Return the most specific :class:`BasePortalFiller` for *url*.

    Hostname matching is used instead of substring search to prevent a URL
    like ``https://evil.com/linkedin.com`` from being treated as LinkedIn.
    """
    try:
        hostname = urlparse(url).hostname or ""
    except ValueError:
        hostname = ""

    filler_cls = _PORTAL_MAP.get(hostname.lower(), GenericFiller)
    return filler_cls(profile=profile, settings=settings)


def fill_application(
    offer: Offer,
    profile: Profile,
    settings: Settings,
    *,
    wait_for_review: bool = True,
) -> None:
    """Open the offer URL in a browser, fill the form, and wait for manual review.

    Pass ``wait_for_review=False`` to skip the blocking ``input()`` prompt
    (useful when calling from the Streamlit UI).  The form is **never**
    submitted automatically regardless of this flag.
    """
    filler = _choose_filler(offer.url, profile, settings)
    filler.run(offer.url, wait_for_review=wait_for_review)
