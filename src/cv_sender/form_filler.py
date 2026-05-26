"""Orchestrates browser-based form filling for a job application."""

from __future__ import annotations

from urllib.parse import urlparse

from cv_sender.config import Profile, Settings
from cv_sender.models import FillResult, FillStatus, Offer
from cv_sender.portals.base import BasePortalFiller
from cv_sender.portals.generic import GenericFiller
from cv_sender.portals.justjoin import JustJoinFiller
from cv_sender.portals.linkedin import LinkedInFiller
from cv_sender.portals.nofluffjobs import NoFluffJobsFiller
from cv_sender.portals.pracuj import PracujFiller
from cv_sender.portals.rocketjobs import RocketJobsFiller

_PORTAL_MAP: dict[str, type[BasePortalFiller]] = {
    "rocketjobs.pl": RocketJobsFiller,
    "www.rocketjobs.pl": RocketJobsFiller,
    "pracuj.pl": PracujFiller,
    "www.pracuj.pl": PracujFiller,
    "linkedin.com": LinkedInFiller,
    "www.linkedin.com": LinkedInFiller,
    "justjoin.it": JustJoinFiller,
    "www.justjoin.it": JustJoinFiller,
    "nofluffjobs.com": NoFluffJobsFiller,
    "www.nofluffjobs.com": NoFluffJobsFiller,
}


def _choose_filler(url: str, profile: Profile, settings: Settings) -> BasePortalFiller:
    """Return the most specific :class:`BasePortalFiller` for *url*.

    Hostname matching is used instead of substring search to prevent a URL
    like ``https://evil.com/linkedin.com`` from being treated as LinkedIn.
    Falls back to :class:`GenericFiller` when no specific filler matches.
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

    This is the **legacy** entry point used by the CLI and by
    ``fill_application_for_offer`` in the service layer.  Use
    :func:`fill_application_with_result` to obtain a structured
    :class:`FillResult`.

    The form is **never** submitted automatically.
    """
    filler = _choose_filler(offer.url, profile, settings)
    filler.run(offer.url, wait_for_review=wait_for_review)


def fill_application_with_result(
    offer: Offer,
    profile: Profile,
    settings: Settings,
    *,
    auto_submit: bool = False,
) -> FillResult:
    """Fill the application form and return a structured :class:`FillResult`.

    Selects the source-specific filler based on the offer URL hostname; falls
    back to :class:`GenericFiller` if no specific filler is registered.

    If the specific filler raises an unhandled exception, a second attempt is
    made with :class:`GenericFiller` and a warning is added to the result.

    ``auto_submit`` must remain ``False`` (the default); the form is **never**
    submitted automatically.
    """
    filler = _choose_filler(offer.url, profile, settings)

    # If the selected filler is already the generic one, just run it
    if isinstance(filler, GenericFiller):
        return filler.fill(offer, auto_submit=auto_submit)

    result = filler.fill(offer, auto_submit=auto_submit)

    # Fallback to GenericFiller if specific filler failed completely
    if result.status == FillStatus.FAILED and result.error:
        result.warnings.append(
            f"Source-specific filler ({filler.__class__.source}) failed: {result.error}. "
            "Falling back to GenericFiller."
        )
        generic_filler = GenericFiller(profile=profile, settings=settings)
        result = generic_filler.fill(offer, auto_submit=auto_submit)

    return result
