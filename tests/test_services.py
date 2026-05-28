"""Tests for the services layer."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cv_sender.models import Application, ApplicationStatus, Decision, Offer
from cv_sender.storage import (
    add_offer,
    load_applications,
    load_offers,
    save_applications,
    save_offers,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_offer(**kwargs) -> Offer:
    defaults = dict(url="https://example.com/job/1", title="Frontend Dev", company="ACME")
    defaults.update(kwargs)
    return Offer(**defaults)


def _make_application(**kwargs) -> Application:
    defaults = dict(offer_id="offer-123", title="Frontend Dev", company="ACME")
    defaults.update(kwargs)
    return Application(**defaults)


@pytest.fixture(autouse=True)
def patch_storage_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect all storage reads/writes to isolated temporary files."""
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_OFFERS", tmp_path / "offers.json")
    monkeypatch.setattr(_storage, "_DEFAULT_APPLICATIONS", tmp_path / "applications.json")


# ---------------------------------------------------------------------------
# add_offer_manual
# ---------------------------------------------------------------------------


def test_add_offer_manual_saves_offer() -> None:
    from cv_sender.services import add_offer_manual

    saved, offer = add_offer_manual(
        url="https://example.com/job/1",
        title="Frontend Dev",
        company="ACME",
        source="manual",
    )

    assert saved is True
    assert offer.title == "Frontend Dev"
    offers = load_offers()
    assert len(offers) == 1
    assert offers[0].url == "https://example.com/job/1"


def test_add_offer_manual_rejects_duplicate_url() -> None:
    from cv_sender.services import add_offer_manual

    add_offer_manual(url="https://example.com/job/1", title="First")
    saved, _ = add_offer_manual(url="https://example.com/job/1", title="Duplicate")

    assert saved is False
    assert len(load_offers()) == 1


def test_add_offer_manual_allows_different_urls() -> None:
    from cv_sender.services import add_offer_manual

    add_offer_manual(url="https://example.com/job/1", title="Job A")
    add_offer_manual(url="https://example.com/job/2", title="Job B")

    assert len(load_offers()) == 2


def test_add_offer_manual_stores_technologies() -> None:
    from cv_sender.services import add_offer_manual

    saved, offer = add_offer_manual(
        url="https://example.com/job/3",
        title="Full-Stack Dev",
        technologies=["React", "TypeScript"],
    )

    assert saved is True
    stored = load_offers()[0]
    assert "React" in stored.technologies
    assert "TypeScript" in stored.technologies


def test_import_offer_from_url_normalizes_slug_title() -> None:
    from cv_sender.services import import_offer_from_url

    draft = SimpleNamespace(
        title="",
        company="",
        location="",
        contract="",
        salary_min=None,
        salary_max=None,
        currency="PLN",
        technologies=[],
        description="",
        extraction_source="pracuj_jsonld",
        extraction_confidence=0.9,
        extraction_warnings=[],
    )

    with patch("cv_sender.extractors.extract_offer", return_value=draft):
        result = import_offer_from_url(
            "https://pracuj.pl/praca/backend-developer,123456",
            auto_score=False,
        )

    assert result.status == "imported"
    stored = load_offers()[0]
    assert stored.title == "Backend Developer"
    assert stored.extraction_source.endswith("url_slug_fallback")
    assert any("slug" in warning.lower() for warning in stored.extraction_warnings)


def test_re_normalize_offers_cleans_existing_titles() -> None:
    from cv_sender.services import re_normalize_offers

    offer = Offer(
        url="https://pracuj.pl/praca/backend-developer,123456",
        title="backend-developer,oferta,123456",
        company="  ACME  ",
    )
    save_offers([offer])

    total, changed = re_normalize_offers()

    assert total == 1
    assert changed == 1
    stored = load_offers()[0]
    assert stored.title == "Backend Developer"
    assert stored.company == "ACME"


def test_sync_all_queue_items_from_offers_delegates_to_apply_queue() -> None:
    from cv_sender.services import sync_all_queue_items_from_offers

    with patch("cv_sender.apply_queue.sync_all_queue_items_from_offers", return_value=3) as mock_sync:
        changed = sync_all_queue_items_from_offers()

    assert changed == 3
    mock_sync.assert_called_once_with()


# ---------------------------------------------------------------------------
# score_offer_by_id
# ---------------------------------------------------------------------------


def test_score_offer_by_id_updates_score() -> None:
    from cv_sender.services import score_offer_by_id

    offer = _make_offer(technologies=["React", "TypeScript"])
    save_offers([offer])

    ok, msg, updated = score_offer_by_id(offer.id, use_llm=False)

    assert ok is True
    assert updated is not None
    assert updated.score is not None
    # Verify the change is persisted
    stored = load_offers()[0]
    assert stored.score == updated.score


def test_score_offer_by_id_sets_decision() -> None:
    from cv_sender.services import score_offer_by_id

    offer = _make_offer(
        title="Frontend Developer",
        technologies=["React", "TypeScript"],
        salary_min=20_000,
        contract="B2B",
    )
    save_offers([offer])

    ok, _, updated = score_offer_by_id(offer.id, use_llm=False)

    assert ok is True
    assert updated is not None
    assert updated.decision in (Decision.APPLY, Decision.MAYBE, Decision.SKIP)


def test_score_offer_by_id_returns_false_for_missing_offer() -> None:
    from cv_sender.services import score_offer_by_id

    ok, msg, updated = score_offer_by_id("nonexistent-id", use_llm=False)

    assert ok is False
    assert updated is None
    assert "not found" in msg.lower()


def test_score_offer_by_id_falls_back_when_llm_unavailable() -> None:
    """LM Studio unavailable should not crash – deterministic score is used."""
    from cv_sender.services import score_offer_by_id

    offer = _make_offer(technologies=["React"])
    save_offers([offer])

    # Patch get_llm_score to simulate unavailability
    with patch("cv_sender.services.get_llm_score", return_value=None):
        ok, msg, updated = score_offer_by_id(offer.id, use_llm=True)

    assert ok is True
    assert updated is not None
    assert updated.score is not None


# ---------------------------------------------------------------------------
# fill_application_for_offer  (Playwright mocked)
# ---------------------------------------------------------------------------


def test_fill_application_creates_application_record() -> None:
    from cv_sender.services import fill_application_for_offer

    offer = _make_offer()
    save_offers([offer])

    with patch("cv_sender.form_filler.fill_application") as mock_fill:
        ok, msg, app = fill_application_for_offer(offer.id)

    assert ok is True
    assert app is not None
    assert app.offer_id == offer.id
    assert app.status == ApplicationStatus.READY_TO_SEND
    assert app.company == offer.company

    stored_apps = load_applications()
    assert len(stored_apps) == 1
    assert stored_apps[0].id == app.id


def test_fill_application_appends_form_filled_event() -> None:
    from cv_sender.services import fill_application_for_offer

    offer = _make_offer()
    save_offers([offer])

    with patch("cv_sender.form_filler.fill_application"):
        ok, _, app = fill_application_for_offer(offer.id)

    assert ok is True
    assert app is not None
    assert any(ev.event == "form_filled" for ev in app.events)


def test_fill_application_creates_failed_record_on_error() -> None:
    from cv_sender.services import fill_application_for_offer

    offer = _make_offer()
    save_offers([offer])

    with patch("cv_sender.form_filler.fill_application", side_effect=RuntimeError("browser crashed")):
        ok, msg, app = fill_application_for_offer(offer.id)

    assert ok is False
    assert "browser crashed" in msg
    assert app is not None
    assert app.status == ApplicationStatus.FAILED
    assert any(ev.event == "fill_failed" for ev in app.events)


def test_fill_application_returns_false_for_missing_offer() -> None:
    from cv_sender.services import fill_application_for_offer

    ok, msg, app = fill_application_for_offer("nonexistent-id")

    assert ok is False
    assert app is None
    assert load_applications() == []


def test_fill_application_updates_existing_record() -> None:
    """Second fill attempt updates the existing application, not creates a new one."""
    from cv_sender.services import fill_application_for_offer

    offer = _make_offer()
    save_offers([offer])

    with patch("cv_sender.form_filler.fill_application"):
        fill_application_for_offer(offer.id)
        fill_application_for_offer(offer.id)

    # Should still be one application record
    stored_apps = load_applications()
    assert len(stored_apps) == 1


# ---------------------------------------------------------------------------
# update_application_status
# ---------------------------------------------------------------------------


def test_update_application_status_persists_change() -> None:
    from cv_sender.services import update_application_status

    app = _make_application()
    save_applications([app])

    ok, msg = update_application_status(app.id, ApplicationStatus.SENT)

    assert ok is True
    stored = load_applications()[0]
    assert stored.status == ApplicationStatus.SENT


def test_update_application_status_appends_event() -> None:
    from cv_sender.services import update_application_status

    app = _make_application(status=ApplicationStatus.NEW)
    save_applications([app])

    update_application_status(app.id, ApplicationStatus.INTERVIEW)

    stored = load_applications()[0]
    assert any(ev.event == "status_changed" for ev in stored.events)
    event = next(ev for ev in stored.events if ev.event == "status_changed")
    assert "interview" in event.details.lower()


def test_update_application_status_returns_false_for_missing() -> None:
    from cv_sender.services import update_application_status

    ok, msg = update_application_status("nonexistent-id", ApplicationStatus.SENT)

    assert ok is False
    assert "not found" in msg.lower()


# ---------------------------------------------------------------------------
# update_application_notes
# ---------------------------------------------------------------------------


def test_update_application_notes_persists() -> None:
    from cv_sender.services import update_application_notes

    app = _make_application()
    save_applications([app])

    ok, msg = update_application_notes(app.id, "Followed up via email on Monday.")

    assert ok is True
    stored = load_applications()[0]
    assert stored.notes == "Followed up via email on Monday."


def test_update_application_notes_returns_false_for_missing() -> None:
    from cv_sender.services import update_application_notes

    ok, msg = update_application_notes("nonexistent-id", "some notes")

    assert ok is False
    assert "not found" in msg.lower()
