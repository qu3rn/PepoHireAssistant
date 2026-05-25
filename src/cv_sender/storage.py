"""JSON-file-based storage for offers and applications."""

from __future__ import annotations

import json
import os
from pathlib import Path

from cv_sender.models import Application, Offer

_DEFAULT_OFFERS = Path(os.getenv("OFFERS_PATH", "data/offers.json"))
_DEFAULT_APPLICATIONS = Path(os.getenv("APPLICATIONS_PATH", "data/applications.json"))


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> list:
    """Read a JSON array from *path*, creating an empty file if necessary."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        return []
    with path.open(encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError:
            return []


def _write_json(path: Path, data: list) -> None:
    """Write a JSON array to *path* with pretty formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------


def load_offers(path: Path | None = None) -> list[Offer]:
    """Load all offers from storage."""
    raw = _read_json(path or _DEFAULT_OFFERS)
    return [Offer.model_validate(item) for item in raw]


def save_offers(offers: list[Offer], path: Path | None = None) -> None:
    """Persist all offers to storage."""
    _write_json(
        path or _DEFAULT_OFFERS,
        [o.model_dump(mode="json") for o in offers],
    )


def add_offer(offer: Offer, path: Path | None = None) -> bool:
    """Add *offer* to storage.

    Returns ``False`` (without saving) if an offer with the same URL already
    exists – this prevents duplicates.
    """
    offers = load_offers(path)
    if any(o.url == offer.url for o in offers):
        return False
    offers.append(offer)
    save_offers(offers, path)
    return True


def get_offer_by_id(offer_id: str, path: Path | None = None) -> Offer | None:
    """Return the offer with *offer_id*, or ``None`` if not found."""
    return next((o for o in load_offers(path) if o.id == offer_id), None)


def update_offer(offer: Offer, path: Path | None = None) -> None:
    """Replace the stored offer that has the same id as *offer*."""
    offers = load_offers(path)
    updated = [offer if o.id == offer.id else o for o in offers]
    save_offers(updated, path)


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


def load_applications(path: Path | None = None) -> list[Application]:
    """Load all applications from storage."""
    raw = _read_json(path or _DEFAULT_APPLICATIONS)
    return [Application.model_validate(item) for item in raw]


def save_applications(applications: list[Application], path: Path | None = None) -> None:
    """Persist all applications to storage."""
    _write_json(
        path or _DEFAULT_APPLICATIONS,
        [a.model_dump(mode="json") for a in applications],
    )


def add_application(application: Application, path: Path | None = None) -> None:
    """Append *application* to storage (duplicates allowed – each run is unique)."""
    apps = load_applications(path)
    apps.append(application)
    save_applications(apps, path)


def get_application_by_id(app_id: str, path: Path | None = None) -> Application | None:
    """Return the application with *app_id*, or ``None`` if not found."""
    return next((a for a in load_applications(path) if a.id == app_id), None)


def update_application(application: Application, path: Path | None = None) -> None:
    """Replace the stored application that has the same id as *application*."""
    apps = load_applications(path)
    updated = [application if a.id == application.id else a for a in apps]
    save_applications(updated, path)
