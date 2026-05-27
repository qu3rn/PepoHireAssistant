"""Tests for collector diagnostics service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cv_sender.collector_diagnostics import (
    REASON_ALREADY_APPLIED,
    REASON_DUPLICATE_URL,
    REASON_EXCLUDED_KEYWORD,
    REASON_IMPORT_FAILED,
    REASON_MISSING_SALARY,
    REASON_NO_KEYWORD_MATCH,
    REASON_NO_TECH_MATCH,
    REASON_SALARY_BELOW_MIN,
    CollectedOfferDecision,
    CollectionDiagnostics,
    SourceSummary,
    collect_with_diagnostics,
    evaluate_collected_offer,
    force_import_collected_offer,
    generate_suggestions,
    get_collection_diagnostics,
    get_latest_collection_diagnostics,
    save_collection_diagnostics,
)
from cv_sender.collectors.base import CollectedOffer, JobSearchCriteria


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_offer(
    url: str = "https://example.com/job/1",
    title: str = "React Developer",
    source: str = "justjoin",
    company: str = "ACME",
    salary_min: float | None = None,
    technologies: list | None = None,
    description: str = "Looking for a React developer",
) -> CollectedOffer:
    return CollectedOffer(
        source=source,
        url=url,
        title=title,
        company=company,
        salary_min=salary_min,
        technologies=["React", "TypeScript"] if technologies is None else technologies,
        description_preview=description,
    )


def _make_criteria(
    keywords: list | None = None,
    technologies: list | None = None,
    min_salary_b2b: int = 0,
    require_salary: bool = False,
    exclude_keywords: list | None = None,
) -> JobSearchCriteria:
    return JobSearchCriteria(
        keywords=["React Developer", "Frontend"] if keywords is None else keywords,
        technologies=["React", "TypeScript"] if technologies is None else technologies,
        min_salary_b2b=min_salary_b2b,
        require_salary=require_salary,
        exclude_keywords=[] if exclude_keywords is None else exclude_keywords,
    )


# ---------------------------------------------------------------------------
# evaluate_collected_offer
# ---------------------------------------------------------------------------


class TestEvaluateCollectedOffer:
    def test_accepted_offer(self):
        offer = _make_offer()
        criteria = _make_criteria()
        decision = evaluate_collected_offer(offer, criteria, set(), set())
        assert decision.decision == "accepted"
        assert not decision.reasons

    def test_duplicate_url(self):
        offer = _make_offer()
        criteria = _make_criteria()
        decision = evaluate_collected_offer(
            offer, criteria, {offer.url}, set()
        )
        assert decision.decision == "duplicate"
        assert REASON_DUPLICATE_URL in decision.reasons

    def test_already_applied(self):
        offer = _make_offer()
        criteria = _make_criteria()
        decision = evaluate_collected_offer(
            offer, criteria, set(), {offer.url}
        )
        assert decision.decision == "duplicate"
        assert REASON_ALREADY_APPLIED in decision.reasons

    def test_missing_salary_when_required(self):
        offer = _make_offer(salary_min=None)
        criteria = _make_criteria(require_salary=True)
        decision = evaluate_collected_offer(offer, criteria, set(), set())
        assert REASON_MISSING_SALARY in decision.reasons
        assert decision.salary_status == "missing"
        assert decision.decision == "rejected"

    def test_salary_below_minimum(self):
        offer = _make_offer(salary_min=5000)
        criteria = _make_criteria(min_salary_b2b=15000)
        decision = evaluate_collected_offer(offer, criteria, set(), set())
        assert REASON_SALARY_BELOW_MIN in decision.reasons
        assert decision.salary_status == "below_minimum"
        assert decision.decision == "rejected"

    def test_salary_ok_when_above_minimum(self):
        offer = _make_offer(salary_min=20000)
        criteria = _make_criteria(min_salary_b2b=15000)
        decision = evaluate_collected_offer(offer, criteria, set(), set())
        assert REASON_SALARY_BELOW_MIN not in decision.reasons
        assert decision.salary_status == "ok"
        assert decision.decision == "accepted"

    def test_missing_salary_no_requirement_is_ok(self):
        offer = _make_offer(salary_min=None)
        criteria = _make_criteria(require_salary=False)
        decision = evaluate_collected_offer(offer, criteria, set(), set())
        assert REASON_MISSING_SALARY not in decision.reasons
        assert decision.salary_status == "missing"
        assert decision.decision == "accepted"

    def test_excluded_keyword_rejects(self):
        offer = _make_offer(description="Senior PHP developer wanted")
        criteria = _make_criteria(exclude_keywords=["PHP"])
        decision = evaluate_collected_offer(offer, criteria, set(), set())
        assert REASON_EXCLUDED_KEYWORD in decision.reasons
        assert decision.decision == "rejected"

    def test_no_required_technology_match(self):
        offer = _make_offer(
            title="Java Backend Developer",
            technologies=["Java", "Spring"],
            description="Java Spring microservices",
        )
        criteria = _make_criteria(
            keywords=["React Developer"],
            technologies=["React", "TypeScript"],
        )
        decision = evaluate_collected_offer(offer, criteria, set(), set())
        assert REASON_NO_TECH_MATCH in decision.reasons

    def test_no_required_keyword_match(self):
        offer = _make_offer(
            title="Database Administrator",
            description="Oracle DBA position",
            technologies=[],   # no React tech
        )
        criteria = _make_criteria(
            keywords=["React Developer", "Frontend"],
            technologies=["React", "TypeScript"],
        )
        decision = evaluate_collected_offer(offer, criteria, set(), set())
        assert REASON_NO_KEYWORD_MATCH in decision.reasons
        assert decision.decision == "rejected"

    def test_matched_keywords_populated(self):
        offer = _make_offer(title="Senior React Developer")
        criteria = _make_criteria(keywords=["React Developer", "Frontend"])
        decision = evaluate_collected_offer(offer, criteria, set(), set())
        assert "React Developer" in decision.matched_keywords

    def test_matched_technologies_populated(self):
        offer = _make_offer(technologies=["React", "TypeScript", "Next.js"])
        criteria = _make_criteria(technologies=["React", "TypeScript"])
        decision = evaluate_collected_offer(offer, criteria, set(), set())
        assert "React" in decision.matched_technologies
        assert "TypeScript" in decision.matched_technologies

    def test_collected_data_stored(self):
        offer = _make_offer()
        criteria = _make_criteria()
        decision = evaluate_collected_offer(offer, criteria, set(), set())
        assert decision.collected_data.get("url") == offer.url
        assert decision.collected_data.get("title") == offer.title


# ---------------------------------------------------------------------------
# generate_suggestions
# ---------------------------------------------------------------------------


class TestGenerateSuggestions:
    def _make_decision(self, decision: str = "rejected", reasons: list | None = None) -> CollectedOfferDecision:
        return CollectedOfferDecision(
            url="https://example.com/job",
            source="justjoin",
            company="ACME",
            title="React Dev",
            decision=decision,  # type: ignore[arg-type]
            reasons=reasons or [],
        )

    def test_no_decisions_gives_empty_queue_suggestion(self):
        suggestions = generate_suggestions([], [])
        assert len(suggestions) > 0
        assert any("No offers" in s for s in suggestions)

    def test_missing_salary_suggestion(self):
        decisions = [
            self._make_decision("rejected", [REASON_MISSING_SALARY]) for _ in range(4)
        ] + [self._make_decision("accepted")]
        suggestions = generate_suggestions(decisions, [])
        assert any("salary" in s.lower() for s in suggestions)

    def test_keyword_mismatch_suggestion(self):
        decisions = [
            self._make_decision("rejected", [REASON_NO_KEYWORD_MATCH]) for _ in range(4)
        ] + [self._make_decision("accepted")]
        suggestions = generate_suggestions(decisions, [])
        assert any("keyword" in s.lower() or "broader" in s.lower() for s in suggestions)

    def test_source_failure_suggestion(self):
        summaries = [SourceSummary(source="pracuj", status="failed", error="connection refused")]
        suggestions = generate_suggestions([self._make_decision("accepted")], summaries)
        assert any("pracuj" in s.lower() for s in suggestions)

    def test_duplicates_suggestion(self):
        decisions = [
            self._make_decision("duplicate", [REASON_DUPLICATE_URL]) for _ in range(5)
        ] + [self._make_decision("accepted")]
        suggestions = generate_suggestions(decisions, [])
        assert any("duplicate" in s.lower() for s in suggestions)

    def test_strict_filters_suggestion(self):
        decisions = [
            self._make_decision("rejected", [REASON_SALARY_BELOW_MIN]) for _ in range(4)
        ] + [self._make_decision("accepted")]
        suggestions = generate_suggestions(decisions, [])
        assert any("salary" in s.lower() or "minimum" in s.lower() for s in suggestions)


# ---------------------------------------------------------------------------
# collect_with_diagnostics — source isolation
# ---------------------------------------------------------------------------


class TestCollectWithDiagnostics:
    def _mock_collector(self, offers: list[CollectedOffer], fail: bool = False):
        c = MagicMock()
        if fail:
            c.search.side_effect = RuntimeError("Connection refused")
        else:
            c.search.return_value = offers
        return c

    def test_source_failure_does_not_break_run(self):
        good_offer = _make_offer()
        criteria = _make_criteria()

        import cv_sender.job_search as js_mod  # noqa: PLC0415

        def get_col_side(name):
            if name == "justjoin":
                return self._mock_collector([good_offer])
            return self._mock_collector([], fail=True)

        original = js_mod._get_collector
        js_mod._get_collector = get_col_side

        with (
            patch("cv_sender.storage.load_offers", return_value=[]),
            patch("cv_sender.storage.load_applications", return_value=[]),
            patch("cv_sender.collector_diagnostics.save_collection_diagnostics"),
            patch("cv_sender.job_search._import_collected_offer", return_value="imported"),
        ):
            try:
                report = collect_with_diagnostics(
                    criteria, ["justjoin", "pracuj"], auto_score=False
                )
            finally:
                js_mod._get_collector = original

        assert len(report.source_summaries) == 2
        failed = next(ss for ss in report.source_summaries if ss.source == "pracuj")
        ok = next(ss for ss in report.source_summaries if ss.source == "justjoin")
        assert failed.status == "failed"
        assert ok.accepted_count == 1

    def test_diagnostics_summary_counts(self):
        criteria = _make_criteria()
        offers = [
            _make_offer(url=f"https://example.com/{i}", title="React Developer") for i in range(3)
        ]

        import cv_sender.job_search as js_mod  # noqa: PLC0415

        collector = self._mock_collector(offers)
        original = js_mod._get_collector
        js_mod._get_collector = lambda name: collector

        with (
            patch("cv_sender.storage.load_offers", return_value=[]),
            patch("cv_sender.storage.load_applications", return_value=[]),
            patch("cv_sender.collector_diagnostics.save_collection_diagnostics"),
            patch("cv_sender.job_search._import_collected_offer", return_value="imported"),
        ):
            try:
                report = collect_with_diagnostics(criteria, ["justjoin"], auto_score=False)
            finally:
                js_mod._get_collector = original

        assert report.total_found == 3
        assert report.total_accepted == 3
        assert report.total_rejected == 0

    def test_duplicate_in_diagnostics(self):
        criteria = _make_criteria()
        offer = _make_offer()
        existing_mock = MagicMock()
        existing_mock.url = offer.url

        import cv_sender.job_search as js_mod  # noqa: PLC0415

        collector = self._mock_collector([offer])
        original = js_mod._get_collector
        js_mod._get_collector = lambda name: collector

        with (
            patch("cv_sender.storage.load_offers", return_value=[existing_mock]),
            patch("cv_sender.storage.load_applications", return_value=[]),
            patch("cv_sender.collector_diagnostics.save_collection_diagnostics"),
        ):
            try:
                report = collect_with_diagnostics(criteria, ["justjoin"], auto_score=False)
            finally:
                js_mod._get_collector = original

        assert report.total_duplicates == 1
        assert report.total_accepted == 0


# ---------------------------------------------------------------------------
# Diagnostics storage
# ---------------------------------------------------------------------------


class TestDiagnosticsStorage:
    def test_save_and_load_latest(self, tmp_path):
        report = CollectionDiagnostics(
            source_summaries=[SourceSummary(source="justjoin", found_count=5, accepted_count=3)],
            suggestions=["Test suggestion"],
        )
        path = tmp_path / "diag.json"
        save_collection_diagnostics(report, path)
        loaded = get_latest_collection_diagnostics(path)
        assert loaded is not None
        assert loaded.run_id == report.run_id
        assert loaded.suggestions == ["Test suggestion"]

    def test_get_by_run_id(self, tmp_path):
        r1 = CollectionDiagnostics()
        r2 = CollectionDiagnostics()
        path = tmp_path / "diag.json"
        save_collection_diagnostics(r1, path)
        save_collection_diagnostics(r2, path)
        loaded = get_collection_diagnostics(r1.run_id, path)
        assert loaded is not None
        assert loaded.run_id == r1.run_id

    def test_max_runs_pruning(self, tmp_path):
        path = tmp_path / "diag.json"
        for _ in range(25):
            save_collection_diagnostics(CollectionDiagnostics(), path)
        import json  # noqa: PLC0415
        with path.open() as fh:
            runs = json.load(fh)
        assert len(runs) == 20  # _MAX_RUNS

    def test_empty_path_returns_none(self, tmp_path):
        result = get_latest_collection_diagnostics(tmp_path / "nonexistent.json")
        assert result is None


# ---------------------------------------------------------------------------
# force_import_collected_offer
# ---------------------------------------------------------------------------


class TestForceImportCollectedOffer:
    def _make_decision(self, url: str = "https://example.com/job/99") -> CollectedOfferDecision:
        return CollectedOfferDecision(
            url=url,
            source="justjoin",
            company="ACME",
            title="React Dev",
            decision="rejected",
            reasons=["missing_salary"],
            collected_data={
                "url": url,
                "source": "justjoin",
                "title": "React Dev",
                "company": "ACME",
                "technologies": ["React"],
                "description_preview": "React role",
                "salary_min": None,
                "salary_max": None,
                "currency": "PLN",
                "contract": "",
                "location": "",
            },
        )

    def test_force_import_saves_offer(self):
        decision = self._make_decision()
        with (
            patch("cv_sender.storage.add_offer", return_value=True),
            patch("cv_sender.storage.update_offer"),
        ):
            ok, msg = force_import_collected_offer(decision, auto_score=False)

        assert ok is True
        assert "React Dev" in msg

    def test_force_import_duplicate_returns_false(self):
        decision = self._make_decision()
        with patch("cv_sender.storage.add_offer", return_value=False):
            ok, msg = force_import_collected_offer(decision, auto_score=False)

        assert ok is False
        assert "already exists" in msg.lower()

    def test_force_import_empty_url_fails(self):
        decision = CollectedOfferDecision(url="", source="justjoin")
        ok, msg = force_import_collected_offer(decision, auto_score=False)
        assert ok is False

    def test_campaign_queue_shortage_diagnostics(self, tmp_path):
        """Queue shortage is visible in diagnostics when no offers were accepted."""
        report = CollectionDiagnostics(
            source_summaries=[
                SourceSummary(source="justjoin", found_count=10, accepted_count=0, rejected_count=10)
            ],
            decisions=[
                CollectedOfferDecision(
                    url=f"https://example.com/{i}",
                    source="justjoin",
                    title="React Dev",
                    company="ACME",
                    decision="rejected",
                    reasons=[REASON_MISSING_SALARY],
                )
                for i in range(10)
            ],
        )
        path = tmp_path / "diag.json"
        save_collection_diagnostics(report, path)
        loaded = get_latest_collection_diagnostics(path)
        assert loaded.total_accepted == 0
        assert loaded.total_rejected == 10
        # Suggestions should mention salary
        sug = generate_suggestions(loaded.decisions, loaded.source_summaries)
        assert any("salary" in s.lower() for s in sug)
