"""Tests for JSON storage helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cv_sender.models import Application, ApplicationStatus, Offer
from cv_sender.storage import (
    add_offer,
    load_applications,
    load_offers,
    save_applications,
    save_offers,
    update_offer,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_offers_path(tmp_path: Path) -> Path:
    return tmp_path / "offers.json"


@pytest.fixture()
def tmp_apps_path(tmp_path: Path) -> Path:
    return tmp_path / "applications.json"


def _make_offer(**kwargs) -> Offer:
    defaults = dict(url="https://example.com/job/1", title="Frontend Dev", company="ACME")
    defaults.update(kwargs)
    return Offer(**defaults)


def _make_application(**kwargs) -> Application:
    defaults = dict(offer_id="offer-123", title="Frontend Dev", company="ACME")
    defaults.update(kwargs)
    return Application(**defaults)


# ---------------------------------------------------------------------------
# Offers – basic CRUD
# ---------------------------------------------------------------------------


def test_save_and_load_offers(tmp_offers_path: Path) -> None:
    offers = [_make_offer(), _make_offer(url="https://example.com/job/2", title="Backend Dev")]
    save_offers(offers, path=tmp_offers_path)

    loaded = load_offers(path=tmp_offers_path)
    assert len(loaded) == 2
    assert loaded[0].title == "Frontend Dev"
    assert loaded[1].title == "Backend Dev"


def test_load_offers_creates_empty_list_when_file_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent.json"
    assert not missing.exists()
    result = load_offers(path=missing)
    assert result == []


def test_save_offers_creates_utf8_json(tmp_offers_path: Path) -> None:
    offer = _make_offer(title="Inżynier Oprogramowania")
    save_offers([offer], path=tmp_offers_path)

    raw = tmp_offers_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data[0]["title"] == "Inżynier Oprogramowania"


# ---------------------------------------------------------------------------
# Duplicate offer detection
# ---------------------------------------------------------------------------


def test_add_offer_returns_true_for_new_offer(tmp_offers_path: Path) -> None:
    offer = _make_offer()
    result = add_offer(offer, path=tmp_offers_path)
    assert result is True
    assert len(load_offers(path=tmp_offers_path)) == 1


def test_add_offer_returns_false_for_duplicate_url(tmp_offers_path: Path) -> None:
    offer = _make_offer()
    add_offer(offer, path=tmp_offers_path)

    duplicate = _make_offer(title="Different Title, Same URL")
    result = add_offer(duplicate, path=tmp_offers_path)
    assert result is False
    # Only the first offer should be stored
    assert len(load_offers(path=tmp_offers_path)) == 1


def test_add_offer_allows_different_urls(tmp_offers_path: Path) -> None:
    add_offer(_make_offer(url="https://example.com/1"), path=tmp_offers_path)
    add_offer(_make_offer(url="https://example.com/2"), path=tmp_offers_path)
    assert len(load_offers(path=tmp_offers_path)) == 2


# ---------------------------------------------------------------------------
# Update offer
# ---------------------------------------------------------------------------


def test_update_offer(tmp_offers_path: Path) -> None:
    offer = _make_offer()
    save_offers([offer], path=tmp_offers_path)

    updated = offer.model_copy(update={"score": 99})
    update_offer(updated, path=tmp_offers_path)

    loaded = load_offers(path=tmp_offers_path)
    assert loaded[0].score == 99


# ---------------------------------------------------------------------------
# Applications – basic CRUD
# ---------------------------------------------------------------------------


def test_save_and_load_applications(tmp_apps_path: Path) -> None:
    apps = [_make_application(), _make_application(offer_id="offer-456")]
    save_applications(apps, path=tmp_apps_path)

    loaded = load_applications(path=tmp_apps_path)
    assert len(loaded) == 2


def test_load_applications_creates_empty_list_when_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    assert load_applications(path=missing) == []


def test_application_status_default(tmp_apps_path: Path) -> None:
    app = _make_application()
    save_applications([app], path=tmp_apps_path)

    loaded = load_applications(path=tmp_apps_path)
    assert loaded[0].status == ApplicationStatus.NEW


# ---------------------------------------------------------------------------
# Application status & notes update
# ---------------------------------------------------------------------------


def test_update_application_status(tmp_apps_path: Path) -> None:
    from cv_sender.storage import update_application

    app = _make_application()
    save_applications([app], path=tmp_apps_path)

    updated = app.model_copy(update={"status": ApplicationStatus.SENT})
    update_application(updated, path=tmp_apps_path)

    loaded = load_applications(path=tmp_apps_path)
    assert loaded[0].status == ApplicationStatus.SENT


def test_update_application_notes(tmp_apps_path: Path) -> None:
    from cv_sender.storage import update_application

    app = _make_application()
    save_applications([app], path=tmp_apps_path)

    updated = app.model_copy(update={"notes": "Sent follow-up email"})
    update_application(updated, path=tmp_apps_path)

    loaded = load_applications(path=tmp_apps_path)
    assert loaded[0].notes == "Sent follow-up email"


def test_update_application_preserves_other_fields(tmp_apps_path: Path) -> None:
    from cv_sender.storage import update_application

    app = _make_application(score=85)
    save_applications([app], path=tmp_apps_path)

    updated = app.model_copy(update={"status": ApplicationStatus.INTERVIEW})
    update_application(updated, path=tmp_apps_path)

    loaded = load_applications(path=tmp_apps_path)
    assert loaded[0].score == 85
    assert loaded[0].status == ApplicationStatus.INTERVIEW


def test_load_applications_does_not_crash_on_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "completely_missing.json"
    result = load_applications(path=missing)
    assert result == []


def test_load_offers_does_not_crash_on_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "completely_missing_offers.json"
    result = load_offers(path=missing)
    assert result == []
