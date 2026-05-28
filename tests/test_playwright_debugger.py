from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from cv_sender.collectors.playwright_base import classify_collected_url
from cv_sender.playwright_debugger import (
    ClassifiedLinkEntry,
    PlaywrightCollectorDebugReport,
    discover_playwright_debug_runs,
    RawLinkWithContext,
    classification_summary_counts,
    suggested_next_fix,
)


def test_debug_report_model_serialization_roundtrip() -> None:
    report = PlaywrightCollectorDebugReport(
        run_id="r1",
        source="rocketjobs",
        keyword="React",
        listing_url="https://rocketjobs.pl/oferty-pracy?q=react",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        headless=False,
    )

    dumped = report.model_dump(mode="json")
    loaded = PlaywrightCollectorDebugReport.model_validate(dumped)

    assert loaded.run_id == "r1"
    assert loaded.source == "rocketjobs"


def test_raw_link_with_context_model_contains_expected_fields() -> None:
    row = RawLinkWithContext(
        href="/oferta-pracy/react-dev",
        absolute_url="https://rocketjobs.pl/oferta-pracy/react-dev",
        visible_text="Senior React Developer",
        parent_text_preview="Acme company remote b2b",
        element_role="link",
        source_listing_url="https://rocketjobs.pl/oferty-pracy",
        attributes={"data-testid": "offer-card"},
    )

    dumped = row.model_dump(mode="json")
    assert dumped["absolute_url"].startswith("https://rocketjobs.pl/oferta-pracy/")
    assert dumped["attributes"]["data-testid"] == "offer-card"


def test_classification_summary_counts() -> None:
    rows = [
        ClassifiedLinkEntry(url="a", classification="job_offer"),
        ClassifiedLinkEntry(url="b", classification="listing"),
        ClassifiedLinkEntry(url="c", classification="company"),
        ClassifiedLinkEntry(url="d", classification="navigation"),
        ClassifiedLinkEntry(url="e", classification="unknown"),
        ClassifiedLinkEntry(url="f", classification="needs_review"),
    ]

    counts = classification_summary_counts(rows)

    assert counts["raw_links_found"] == 6
    assert counts["job_offer"] == 1
    assert counts["listing"] == 1
    assert counts["navigation_company"] == 2
    assert counts["unknown"] == 1
    assert counts["needs_review"] == 1


def test_suggestion_raw_links_but_zero_job_offer() -> None:
    msg = suggested_next_fix(
        raw_links_count=25,
        job_offer_count=0,
        listing_count=10,
        job_card_candidates_count=0,
        links_before_scroll=10,
        new_links_per_scroll=[2, 0, 0],
        login_or_captcha_or_blocked=False,
    )
    assert "classifier likely too strict" in msg.lower()


def test_suggestion_cards_but_no_links() -> None:
    msg = suggested_next_fix(
        raw_links_count=0,
        job_offer_count=0,
        listing_count=0,
        job_card_candidates_count=8,
        links_before_scroll=0,
        new_links_per_scroll=[0, 0],
        login_or_captcha_or_blocked=False,
    )
    assert "client-side navigation" in msg.lower()


def test_suggestion_mostly_listing_urls() -> None:
    msg = suggested_next_fix(
        raw_links_count=40,
        job_offer_count=5,
        listing_count=30,
        job_card_candidates_count=2,
        links_before_scroll=20,
        new_links_per_scroll=[3, 2],
        login_or_captcha_or_blocked=False,
    )
    assert "seo/category" in msg.lower()


def test_suggestion_no_new_links_after_scroll() -> None:
    msg = suggested_next_fix(
        raw_links_count=10,
        job_offer_count=3,
        listing_count=2,
        job_card_candidates_count=1,
        links_before_scroll=10,
        new_links_per_scroll=[0, 0, 0],
        login_or_captcha_or_blocked=False,
    )
    assert "infinite scroll" in msg.lower()


def test_rocketjobs_offer_and_listing_classification_remain_stable() -> None:
    offer = classify_collected_url("rocketjobs", "https://rocketjobs.pl/oferta-pracy/frontend-engineer-react-remote")
    listing = classify_collected_url("rocketjobs", "https://rocketjobs.pl/oferty-pracy/krakow")

    assert offer.type == "job_offer"
    assert listing.type == "listing"


def test_justjoin_offer_and_listing_classification_remain_stable() -> None:
    offer = classify_collected_url("justjoin", "https://justjoin.it/job-offer/senior-frontend-react-dev-warsaw")
    listing = classify_collected_url("justjoin", "https://justjoin.it/job-offers/all-locations/javascript")

    assert offer.type == "job_offer"
    assert listing.type == "listing"


def test_discover_playwright_debug_runs_reads_latest_runs(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1" / "rocketjobs"
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "source": "rocketjobs",
                "started_at": "2026-05-28T12:00:00+00:00",
                "keyword": "React",
                "listing_url": "https://rocketjobs.pl/oferty-pracy?q=react",
                "status": "partial",
                "raw_link_count": 12,
                "job_offer_count": 3,
                "listing_count": 4,
                "needs_review_count": 1,
                "unknown_count": 2,
                "modal_summary": {
                    "handler_called": True,
                    "cookie_banner_visible_before": True,
                    "cookie_banner_visible_after": False,
                    "actions_count": 2,
                    "actions_success_count": 1,
                    "warnings": ["Cookie banner still visible after modal handler."],
                },
                "detected_captcha": False,
                "detected_login_wall": False,
                "detected_blocked_page": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "modal_actions.json").write_text("[]", encoding="utf-8")

    runs = discover_playwright_debug_runs(limit=10, base_dir=tmp_path)

    assert len(runs) == 1
    assert runs[0].run_id == "run-1"
    assert runs[0].handler_called is True
    assert runs[0].modal_actions_count == 2


def test_discover_playwright_debug_runs_handles_missing_metadata(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-2" / "justjoin"
    run_dir.mkdir(parents=True)
    (run_dir / "links.json").write_text(json.dumps({"links": []}), encoding="utf-8")

    runs = discover_playwright_debug_runs(limit=10, base_dir=tmp_path)

    assert len(runs) == 1
    assert runs[0].run_id == "run-2"
    assert runs[0].source == "justjoin"
    assert runs[0].metadata_missing is True
    assert any("metadata.json missing" in warning for warning in runs[0].warnings)


def test_discover_playwright_debug_runs_uses_modal_summary_from_metadata(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-3" / "rocketjobs"
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "run_id": "run-3",
                "source": "rocketjobs",
                "timestamp": "2026-05-28T13:00:00+00:00",
                "modal_summary": {
                    "handler_called": True,
                    "cookie_banner_visible_before": True,
                    "cookie_banner_visible_after": True,
                    "actions_count": 4,
                    "actions_success_count": 3,
                    "warnings": ["Cookie banner still visible after modal handler."],
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    runs = discover_playwright_debug_runs(limit=10, base_dir=tmp_path)

    assert runs[0].handler_called is True
    assert runs[0].cookie_banner_visible_before is True
    assert runs[0].cookie_banner_visible_after is True
    assert runs[0].modal_actions_count == 4
