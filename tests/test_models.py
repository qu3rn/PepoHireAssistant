"""Tests for Pydantic model validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cv_sender.models import Application, ApplicationEvent, ApplicationStatus, Decision, Offer

# ---------------------------------------------------------------------------
# Offer model
# ---------------------------------------------------------------------------


def test_offer_requires_url_and_title() -> None:
    # Empty strings are valid – Pydantic does not enforce non-empty by default
    offer = Offer(url="", title="")
    assert offer.url == ""
    assert offer.title == ""
    # Missing required positional fields raise a ValidationError
    with pytest.raises((ValidationError, TypeError)):
        Offer()  # type: ignore[call-arg]


def test_offer_default_values() -> None:
    offer = Offer(url="https://example.com/job", title="Dev")
    assert offer.currency == "PLN"
    assert offer.technologies == []
    assert offer.score is None
    assert offer.decision is None
    assert offer.decision_reasons == []
    assert offer.risks == []
    assert offer.id  # auto-generated UUID


def test_offer_id_is_unique() -> None:
    o1 = Offer(url="https://a.com", title="Dev A")
    o2 = Offer(url="https://b.com", title="Dev B")
    assert o1.id != o2.id


def test_offer_decision_enum_validation() -> None:
    offer = Offer(url="https://example.com", title="Dev", decision="apply")
    assert offer.decision == Decision.APPLY


def test_offer_invalid_decision_raises() -> None:
    with pytest.raises(ValidationError):
        Offer(url="https://example.com", title="Dev", decision="invalid")


def test_offer_salary_optional() -> None:
    offer = Offer(url="https://x.com", title="Dev", salary_min=None, salary_max=None)
    assert offer.salary_min is None
    assert offer.salary_max is None


# ---------------------------------------------------------------------------
# Application model
# ---------------------------------------------------------------------------


def test_application_requires_offer_id() -> None:
    with pytest.raises((ValidationError, TypeError)):
        Application()  # type: ignore[call-arg]


def test_application_default_status() -> None:
    app = Application(offer_id="abc-123")
    assert app.status == ApplicationStatus.NEW


def test_application_status_enum_values() -> None:
    valid_statuses = [
        "new", "matched", "skipped", "ready_to_send",
        "sent", "failed", "reply_received", "interview", "rejected", "offer",
    ]
    for status in valid_statuses:
        app = Application(offer_id="x", status=status)
        assert app.status == ApplicationStatus(status)


def test_application_invalid_status_raises() -> None:
    with pytest.raises(ValidationError):
        Application(offer_id="x", status="unknown_status")


def test_application_events_default_empty() -> None:
    app = Application(offer_id="x")
    assert app.events == []


def test_application_event_model() -> None:
    event = ApplicationEvent(event="form_filled", details="Test")
    assert event.event == "form_filled"
    assert event.timestamp is not None


# ---------------------------------------------------------------------------
# Round-trip serialisation
# ---------------------------------------------------------------------------


def test_offer_round_trip() -> None:
    offer = Offer(
        url="https://example.com",
        title="Python Dev",
        company="ACME",
        technologies=["Python", "Django"],
        score=75,
        decision=Decision.APPLY,
    )
    data = offer.model_dump(mode="json")
    restored = Offer.model_validate(data)
    assert restored.id == offer.id
    assert restored.decision == Decision.APPLY
    assert restored.technologies == ["Python", "Django"]


def test_application_round_trip() -> None:
    app = Application(
        offer_id="offer-1",
        status=ApplicationStatus.SENT,
        events=[ApplicationEvent(event="sent", details="Manual send")],
    )
    data = app.model_dump(mode="json")
    restored = Application.model_validate(data)
    assert restored.status == ApplicationStatus.SENT
    assert len(restored.events) == 1
    assert restored.events[0].event == "sent"
