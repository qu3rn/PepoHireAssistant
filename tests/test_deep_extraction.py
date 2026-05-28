"""Tests for deep offer detail extraction and completeness checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from cv_sender.models import DeepExtractionResult, DeepExtractionStatus, Offer
from cv_sender.storage import load_offers, save_offers


def _make_offer(**kwargs) -> Offer:
    defaults = dict(
        id="offer-1",
        url="https://example.com/job/1",
        source="manual",
        title="Frontend Developer",
        company="ACME",
        location="Warsaw",
        contract="B2B",
        salary_min=18000,
        salary_max=22000,
        salary_raw_text="18 000 - 22 000 PLN",
        salary_confidence=0.9,
        technologies=["React", "TypeScript", "Next.js"],
        description="A" * 500,
    )
    defaults.update(kwargs)
    return Offer(**defaults)


@pytest.fixture(autouse=True)
def patch_storage_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_OFFERS", tmp_path / "offers.json")
    monkeypatch.setattr(_storage, "_DEFAULT_APPLY_QUEUE", tmp_path / "apply_queue.json")


def test_is_offer_incomplete_detects_missing_salary_tech_description() -> None:
    from cv_sender.deep_extraction import is_offer_incomplete

    offer = _make_offer(
        salary_min=None,
        salary_max=None,
        salary_raw_text="",
        salary_confidence=0.0,
        technologies=[],
        description="too short",
    )

    result = is_offer_incomplete(offer)

    assert result.is_incomplete is True
    assert "salary" in result.missing_fields
    assert "technologies" in result.missing_fields
    assert "description" in result.weak_fields or "description" in result.missing_fields


def test_deep_extract_skips_complete_offer_when_not_forced() -> None:
    from cv_sender.deep_extraction import deep_extract_offer_details

    save_offers([_make_offer()])

    result = deep_extract_offer_details("offer-1", force=False)

    assert result.status == DeepExtractionStatus.SKIPPED_COMPLETE


def test_deep_extract_updates_missing_salary(monkeypatch: pytest.MonkeyPatch) -> None:
    import cv_sender.deep_extraction as dx

    save_offers([_make_offer(salary_min=None, salary_max=None, salary_raw_text="", salary_confidence=0.0)])

    monkeypatch.setattr(dx, "_extract_with_playwright", lambda offer, run_dir: dx._PageExtractionPayload(html="<html></html>", visible_text=""))
    monkeypatch.setattr(
        dx,
        "_extract_raw_fields",
        lambda offer, payload: (
            {
                "title": "Frontend Developer",
                "company": "ACME",
                "salary_min": 21000,
                "salary_max": 24000,
                "salary_raw_text": "21 000 - 24 000 PLN",
                "location": "Warsaw",
                "contract": "B2B",
                "technologies": ["React", "TypeScript"],
                "description": "B" * 600,
                "extraction_source": "test",
                "extraction_confidence": 0.9,
            },
            "test_extractor",
            [],
        ),
    )
    monkeypatch.setattr(dx, "_rescore_offer", lambda offer: offer.model_copy(update={"score": 88}))
    monkeypatch.setattr(dx, "sync_all_queue_items_from_offers", lambda: 1)
    monkeypatch.setattr(dx, "_persist_debug_artifacts", lambda **kwargs: None)

    result = dx.deep_extract_offer_details("offer-1")

    stored = load_offers()[0]
    assert result.status == DeepExtractionStatus.UPDATED
    assert stored.salary_min == 21000
    assert stored.salary_max == 24000


def test_title_slug_replaced_by_clean_title(monkeypatch: pytest.MonkeyPatch) -> None:
    import cv_sender.deep_extraction as dx

    save_offers([_make_offer(title="frontend-developer-react-12345")])

    monkeypatch.setattr(dx, "_extract_with_playwright", lambda offer, run_dir: dx._PageExtractionPayload(html="<html></html>", visible_text=""))
    monkeypatch.setattr(
        dx,
        "_extract_raw_fields",
        lambda offer, payload: (
            {
                "title": "Senior Frontend Developer",
                "company": offer.company,
                "description": offer.description,
                "technologies": offer.technologies,
                "location": offer.location,
                "contract": offer.contract,
                "salary_min": offer.salary_min,
                "salary_max": offer.salary_max,
                "extraction_source": "test",
                "extraction_confidence": 0.9,
            },
            "test_extractor",
            [],
        ),
    )
    monkeypatch.setattr(dx, "_rescore_offer", lambda offer: offer)
    monkeypatch.setattr(dx, "sync_all_queue_items_from_offers", lambda: 0)
    monkeypatch.setattr(dx, "_persist_debug_artifacts", lambda **kwargs: None)

    dx.deep_extract_offer_details("offer-1", force=True)

    stored = load_offers()[0]
    assert stored.title == "Senior Frontend Developer"


def test_technologies_are_merged_and_deduplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    import cv_sender.deep_extraction as dx

    save_offers([_make_offer(technologies=["React", "TypeScript"])])

    monkeypatch.setattr(dx, "_extract_with_playwright", lambda offer, run_dir: dx._PageExtractionPayload(html="<html></html>", visible_text=""))
    monkeypatch.setattr(
        dx,
        "_extract_raw_fields",
        lambda offer, payload: (
            {
                "title": offer.title,
                "company": offer.company,
                "description": offer.description,
                "technologies": ["React", "Next.js", "next.js"],
                "location": offer.location,
                "contract": offer.contract,
                "salary_min": offer.salary_min,
                "salary_max": offer.salary_max,
                "extraction_source": "test",
                "extraction_confidence": 0.8,
            },
            "test_extractor",
            [],
        ),
    )
    monkeypatch.setattr(dx, "_rescore_offer", lambda offer: offer)
    monkeypatch.setattr(dx, "sync_all_queue_items_from_offers", lambda: 0)
    monkeypatch.setattr(dx, "_persist_debug_artifacts", lambda **kwargs: None)

    dx.deep_extract_offer_details("offer-1", force=True)

    stored = load_offers()[0]
    assert sorted(stored.technologies) == sorted(["React", "TypeScript", "Next.js"])


def test_good_description_is_not_overwritten_by_shorter(monkeypatch: pytest.MonkeyPatch) -> None:
    import cv_sender.deep_extraction as dx

    long_desc = "A" * 900
    save_offers([_make_offer(description=long_desc)])

    monkeypatch.setattr(dx, "_extract_with_playwright", lambda offer, run_dir: dx._PageExtractionPayload(html="<html></html>", visible_text=""))
    monkeypatch.setattr(
        dx,
        "_extract_raw_fields",
        lambda offer, payload: (
            {
                "title": offer.title,
                "company": offer.company,
                "description": "short noisy text",
                "technologies": offer.technologies,
                "location": offer.location,
                "contract": offer.contract,
                "salary_min": offer.salary_min,
                "salary_max": offer.salary_max,
                "extraction_source": "test",
                "extraction_confidence": 0.9,
            },
            "test_extractor",
            [],
        ),
    )
    monkeypatch.setattr(dx, "_rescore_offer", lambda offer: offer)
    monkeypatch.setattr(dx, "sync_all_queue_items_from_offers", lambda: 0)
    monkeypatch.setattr(dx, "_persist_debug_artifacts", lambda **kwargs: None)

    dx.deep_extract_offer_details("offer-1")

    stored = load_offers()[0]
    assert len(stored.description) >= 900


def test_rescore_called_after_update(monkeypatch: pytest.MonkeyPatch) -> None:
    import cv_sender.deep_extraction as dx

    save_offers([_make_offer(salary_min=None, salary_max=None, salary_raw_text="")])

    called = {"rescore": 0}

    def _rescore(offer: Offer) -> Offer:
        called["rescore"] += 1
        return offer.model_copy(update={"score": 77})

    monkeypatch.setattr(dx, "_extract_with_playwright", lambda offer, run_dir: dx._PageExtractionPayload(html="<html></html>", visible_text=""))
    monkeypatch.setattr(
        dx,
        "_extract_raw_fields",
        lambda offer, payload: (
            {
                "title": offer.title,
                "company": offer.company,
                "salary_min": 19000,
                "salary_max": 22000,
                "description": offer.description,
                "technologies": offer.technologies,
                "location": offer.location,
                "contract": offer.contract,
                "extraction_source": "test",
                "extraction_confidence": 0.9,
            },
            "test_extractor",
            [],
        ),
    )
    monkeypatch.setattr(dx, "_rescore_offer", _rescore)
    monkeypatch.setattr(dx, "sync_all_queue_items_from_offers", lambda: 0)
    monkeypatch.setattr(dx, "_persist_debug_artifacts", lambda **kwargs: None)

    result = dx.deep_extract_offer_details("offer-1")

    assert result.status == DeepExtractionStatus.UPDATED
    assert called["rescore"] == 1


def test_queue_sync_called_after_update(monkeypatch: pytest.MonkeyPatch) -> None:
    import cv_sender.deep_extraction as dx

    save_offers([_make_offer(salary_min=None, salary_max=None, salary_raw_text="")])

    called = {"sync": 0}

    monkeypatch.setattr(dx, "_extract_with_playwright", lambda offer, run_dir: dx._PageExtractionPayload(html="<html></html>", visible_text=""))
    monkeypatch.setattr(
        dx,
        "_extract_raw_fields",
        lambda offer, payload: (
            {
                "title": offer.title,
                "company": offer.company,
                "salary_min": 20000,
                "salary_max": 23000,
                "description": offer.description,
                "technologies": offer.technologies,
                "location": offer.location,
                "contract": offer.contract,
                "extraction_source": "test",
                "extraction_confidence": 0.9,
            },
            "test_extractor",
            [],
        ),
    )
    monkeypatch.setattr(dx, "_rescore_offer", lambda offer: offer)
    monkeypatch.setattr(dx, "sync_all_queue_items_from_offers", lambda: called.__setitem__("sync", called["sync"] + 1) or 1)
    monkeypatch.setattr(dx, "_persist_debug_artifacts", lambda **kwargs: None)

    dx.deep_extract_offer_details("offer-1")

    assert called["sync"] == 1


def test_blocked_page_returns_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    import cv_sender.deep_extraction as dx

    save_offers([_make_offer(salary_min=None, salary_max=None, salary_raw_text="")])

    monkeypatch.setattr(
        dx,
        "_extract_with_playwright",
        lambda offer, run_dir: dx._PageExtractionPayload(blocked=True, blocked_reason="captcha_detected"),
    )
    monkeypatch.setattr(dx, "_persist_debug_artifacts", lambda **kwargs: None)

    result = dx.deep_extract_offer_details("offer-1")

    assert result.status == DeepExtractionStatus.BLOCKED


def test_batch_processes_only_incomplete_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    import cv_sender.deep_extraction as dx

    complete = _make_offer(id="complete")
    incomplete = _make_offer(
        id="incomplete",
        url="https://example.com/job/2",
        salary_min=None,
        salary_max=None,
        salary_raw_text="",
        technologies=[],
        description="short",
    )
    save_offers([complete, incomplete])

    calls: list[str] = []

    def _fake_inner(*, offer_id: str, force: bool, use_playwright: bool, run_id: str) -> DeepExtractionResult:
        calls.append(offer_id)
        return DeepExtractionResult(offer_id=offer_id, status=DeepExtractionStatus.NO_CHANGE)

    monkeypatch.setattr(dx, "_deep_extract_offer_details", _fake_inner)

    result = dx.deep_extract_offers(["complete", "incomplete"], only_incomplete=True)

    assert len(result.results) == 2
    assert calls == ["incomplete"]
    assert any(r.status == DeepExtractionStatus.SKIPPED_COMPLETE for r in result.results)
