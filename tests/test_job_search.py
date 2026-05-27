"""Tests for collectors base module — filtering logic, criteria helpers, and base class."""

from __future__ import annotations

import pytest

from cv_sender.collectors.base import (
    CollectedOffer,
    JobSearchCriteria,
    JobCollectionResult,
    passes_criteria_filter,
    BaseJobCollector,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_offer(**kwargs) -> CollectedOffer:
    defaults = dict(
        source="test",
        url="https://example.com/job/1",
        title="React Developer",
        company="ACME",
        location="Remote",
        technologies=["React", "TypeScript"],
        description_preview="We build great apps with React and TypeScript.",
    )
    defaults.update(kwargs)
    return CollectedOffer(**defaults)


def _make_criteria(**kwargs) -> JobSearchCriteria:
    defaults = dict(
        keywords=["React Developer", "Frontend"],
        technologies=["React", "TypeScript"],
        locations=["Remote"],
        seniority=[],
        contract_types=["B2B"],
        min_salary_b2b=0,
        require_salary=False,
        max_offers_per_source=30,
        max_total_offers=100,
        exclude_keywords=[],
    )
    defaults.update(kwargs)
    return JobSearchCriteria(**defaults)


# ---------------------------------------------------------------------------
# passes_criteria_filter
# ---------------------------------------------------------------------------


class TestPassesCriteriaFilter:
    def test_keyword_match_in_title_passes(self):
        offer = _make_offer(title="Senior React Developer")
        criteria = _make_criteria(keywords=["React Developer"])
        assert passes_criteria_filter(offer, criteria) == ""

    def test_keyword_match_in_description_passes(self):
        offer = _make_offer(title="Engineer", description_preview="We need a React Developer asap")
        criteria = _make_criteria(keywords=["React Developer"])
        assert passes_criteria_filter(offer, criteria) == ""

    def test_technology_match_passes_when_no_keyword_match(self):
        offer = _make_offer(title="Software Engineer", technologies=["React"])
        criteria = _make_criteria(keywords=["Backend Python"], technologies=["React"])
        assert passes_criteria_filter(offer, criteria) == ""

    def test_no_keyword_and_no_tech_match_fails(self):
        offer = _make_offer(title="Java Developer", technologies=["Spring"], description_preview="backend")
        criteria = _make_criteria(keywords=["React Developer"], technologies=["React"])
        reason = passes_criteria_filter(offer, criteria)
        assert reason != ""
        assert "keyword" in reason.lower() or "technology" in reason.lower()

    def test_exclude_keyword_in_title_fails(self):
        offer = _make_offer(title="Angular Developer", technologies=["Angular"])
        criteria = _make_criteria(
            keywords=["Frontend Developer"],
            technologies=["Angular"],
            exclude_keywords=["Angular"],
        )
        reason = passes_criteria_filter(offer, criteria)
        assert "excluded keyword" in reason.lower()

    def test_exclude_keyword_in_description_fails(self):
        offer = _make_offer(
            title="React Developer",
            description_preview="Must know WordPress and PHP",
        )
        criteria = _make_criteria(exclude_keywords=["WordPress"])
        reason = passes_criteria_filter(offer, criteria)
        assert "excluded keyword" in reason.lower()

    def test_require_salary_passes_when_salary_present(self):
        offer = _make_offer(salary_min=15000.0, salary_max=25000.0)
        criteria = _make_criteria(require_salary=True)
        assert passes_criteria_filter(offer, criteria) == ""

    def test_require_salary_fails_when_no_salary(self):
        offer = _make_offer(salary_min=None, salary_max=None)
        criteria = _make_criteria(require_salary=True)
        reason = passes_criteria_filter(offer, criteria)
        assert "salary" in reason.lower()

    def test_min_salary_b2b_below_minimum_fails(self):
        offer = _make_offer(salary_min=8000.0)
        criteria = _make_criteria(min_salary_b2b=15000)
        reason = passes_criteria_filter(offer, criteria)
        assert "below minimum" in reason.lower()

    def test_min_salary_b2b_above_minimum_passes(self):
        offer = _make_offer(salary_min=20000.0)
        criteria = _make_criteria(min_salary_b2b=15000)
        assert passes_criteria_filter(offer, criteria) == ""

    def test_min_salary_no_salary_info_passes_when_not_required(self):
        offer = _make_offer(salary_min=None, salary_max=None)
        criteria = _make_criteria(min_salary_b2b=15000, require_salary=False)
        # No salary info → can't filter by minimum, should pass
        assert passes_criteria_filter(offer, criteria) == ""

    def test_case_insensitive_keyword_match(self):
        offer = _make_offer(title="senior react developer")
        criteria = _make_criteria(keywords=["React Developer"])
        assert passes_criteria_filter(offer, criteria) == ""

    def test_case_insensitive_exclude(self):
        offer = _make_offer(title="PHP DEVELOPER")
        criteria = _make_criteria(keywords=["Developer"], exclude_keywords=["php"])
        reason = passes_criteria_filter(offer, criteria)
        assert reason != ""

    def test_empty_keywords_and_technologies_fails(self):
        offer = _make_offer(title="React Developer")
        criteria = _make_criteria(keywords=[], technologies=[])
        reason = passes_criteria_filter(offer, criteria)
        assert reason != ""

    def test_technology_in_description_preview_matches(self):
        offer = _make_offer(
            title="Engineer",
            technologies=[],
            description_preview="We heavily use React and TypeScript",
        )
        criteria = _make_criteria(keywords=["Backend"], technologies=["React"])
        assert passes_criteria_filter(offer, criteria) == ""


# ---------------------------------------------------------------------------
# JobSearchCriteria.from_config
# ---------------------------------------------------------------------------


class TestJobSearchCriteriaFromConfig:
    def test_from_config_maps_all_fields(self):
        from cv_sender.config import JobSearchConfig  # noqa: PLC0415

        cfg = JobSearchConfig(
            keywords=["React"],
            technologies=["TypeScript"],
            locations=["Remote"],
            seniority=["Senior"],
            contract_types=["B2B"],
            min_salary_b2b=10000,
            require_salary=True,
            max_offers_per_source=20,
            max_total_offers=50,
            exclude_keywords=["PHP"],
            request_delay_seconds=2.0,
        )
        criteria = JobSearchCriteria.from_config(cfg)
        assert criteria.keywords == ["React"]
        assert criteria.technologies == ["TypeScript"]
        assert criteria.min_salary_b2b == 10000
        assert criteria.require_salary is True
        assert criteria.max_offers_per_source == 20
        assert criteria.exclude_keywords == ["PHP"]
        assert criteria.request_delay_seconds == 2.0


class TestEmergencyReactCriteria:
    def test_emergency_react_has_react_keyword(self):
        criteria = JobSearchCriteria.emergency_react()
        assert any("react" in k.lower() for k in criteria.keywords)

    def test_emergency_react_has_typescript(self):
        criteria = JobSearchCriteria.emergency_react()
        assert any("typescript" in t.lower() for t in criteria.technologies)

    def test_emergency_react_has_remote_location(self):
        criteria = JobSearchCriteria.emergency_react()
        assert any("remote" in loc.lower() for loc in criteria.locations)


# ---------------------------------------------------------------------------
# BaseJobCollector.collect_and_filter
# ---------------------------------------------------------------------------


class TestCollectAndFilter:
    def test_successful_search_returns_result(self):
        class FakeCollector(BaseJobCollector):
            source = "fake"

            def search(self, criteria):
                return [
                    _make_offer(url="https://example.com/1", title="React Developer"),
                    _make_offer(url="https://example.com/2", title="PHP Developer"),
                ]

        collector = FakeCollector()
        criteria = _make_criteria(keywords=["React Developer"], exclude_keywords=[])
        result = collector.collect_and_filter(criteria)

        assert result.source == "fake"
        assert result.collected_count == 2
        assert len(result.offers) == 2

    def test_exception_in_search_returns_error_result(self):
        class BrokenCollector(BaseJobCollector):
            source = "broken"

            def search(self, criteria):
                raise RuntimeError("Network error!")

        collector = BrokenCollector()
        criteria = _make_criteria()
        result = collector.collect_and_filter(criteria)

        assert result.source == "broken"
        assert result.collected_count == 0
        assert len(result.errors) == 1
        assert "Network error" in result.errors[0]

    def test_one_source_failure_does_not_affect_others(self):
        """Verifies the contract: each collector must catch its own errors."""
        class GoodCollector(BaseJobCollector):
            source = "good"

            def search(self, criteria):
                return [_make_offer(url="https://example.com/g")]

        class BadCollector(BaseJobCollector):
            source = "bad"

            def search(self, criteria):
                raise ConnectionError("Timeout")

        good_result = GoodCollector().collect_and_filter(_make_criteria())
        bad_result = BadCollector().collect_and_filter(_make_criteria())

        assert good_result.collected_count == 1
        assert len(good_result.errors) == 0
        assert bad_result.collected_count == 0
        assert len(bad_result.errors) == 1

    def test_filtered_offers_counted_as_skipped(self):
        class FakeCollector(BaseJobCollector):
            source = "fake"

            def search(self, criteria):
                return [
                    _make_offer(
                        title="PHP Developer",
                        technologies=["PHP"],
                        description_preview="PHP backend development",
                    ),
                ]

        collector = FakeCollector()
        criteria = _make_criteria(keywords=["React Developer"], technologies=["React"])
        result = collector.collect_and_filter(criteria)

        assert result.skipped_count == 1
        assert result.offers[0].skip_reason != ""
