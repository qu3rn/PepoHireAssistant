"""Tests for the listing.py pagination / filtering / sorting utilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


import pytest

from cv_sender.listing import (
    ListQuery,
    ListResult,
    apply_filters,
    apply_search,
    apply_sort,
    build_list_result,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@dataclass
class _Item:
    id: int
    title: str
    company: str
    score: int | None = None
    status: str = "new"
    source: str = "rocketjobs"
    created_at: str | None = None


def _make_items(n: int) -> list[_Item]:
    sources = ["rocketjobs", "justjoin", "pracuj"]
    statuses = ["new", "sent", "archived"]
    return [
        _Item(
            id=i,
            title=f"Job {i}",
            company=f"Company {i % 5}",
            score=i * 3 if i % 4 != 0 else None,
            status=statuses[i % 3],
            source=sources[i % 3],
            created_at=f"2024-0{(i % 9) + 1}-01",
        )
        for i in range(1, n + 1)
    ]


# ---------------------------------------------------------------------------
# Pagination tests
# ---------------------------------------------------------------------------


class TestPagination:
    def test_first_page_item_count(self) -> None:
        items = _make_items(60)
        query = ListQuery(page=1, page_size=25)
        result = build_list_result(items, query)
        assert len(result.items) == 25

    def test_last_page_may_have_fewer_items(self) -> None:
        items = _make_items(27)
        query = ListQuery(page=2, page_size=25)
        result = build_list_result(items, query)
        assert len(result.items) == 2

    def test_total_pages_calculation(self) -> None:
        assert build_list_result(_make_items(25), ListQuery(page_size=25)).total_pages == 1
        assert build_list_result(_make_items(26), ListQuery(page_size=25)).total_pages == 2
        assert build_list_result(_make_items(100), ListQuery(page_size=25)).total_pages == 4
        assert build_list_result(_make_items(101), ListQuery(page_size=25)).total_pages == 5

    def test_total_count_reflects_all_items(self) -> None:
        items = _make_items(40)
        result = build_list_result(items, ListQuery(page=1, page_size=10))
        assert result.total_count == 40

    def test_empty_list_returns_page_1(self) -> None:
        result = build_list_result([], ListQuery())
        assert result.page == 1
        assert result.total_pages == 1
        assert result.items == []
        assert result.start_index == 0
        assert result.end_index == 0

    def test_page_beyond_total_clamps_to_last(self) -> None:
        items = _make_items(10)
        result = build_list_result(items, ListQuery(page=999, page_size=25))
        assert result.page == 1
        assert len(result.items) == 10

    def test_has_prev_and_has_next(self) -> None:
        items = _make_items(50)
        r1 = build_list_result(items, ListQuery(page=1, page_size=25))
        assert not r1.has_prev
        assert r1.has_next

        r2 = build_list_result(items, ListQuery(page=2, page_size=25))
        assert r2.has_prev
        assert not r2.has_next

    def test_start_end_index(self) -> None:
        items = _make_items(30)
        r = build_list_result(items, ListQuery(page=2, page_size=10))
        assert r.start_index == 11
        assert r.end_index == 20

    def test_only_current_page_items_returned(self) -> None:
        """Items on page 2 must not overlap with page 1 items."""
        items = _make_items(50)
        r1 = build_list_result(items, ListQuery(page=1, page_size=25))
        r2 = build_list_result(items, ListQuery(page=2, page_size=25))
        ids1 = {i.id for i in r1.items}
        ids2 = {i.id for i in r2.items}
        assert ids1.isdisjoint(ids2)
        assert len(ids1) + len(ids2) == 50


# ---------------------------------------------------------------------------
# Search tests
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_by_title(self) -> None:
        items = _make_items(10)
        result = apply_search(items, "Job 5", ["title"])
        assert len(result) == 1
        assert result[0].title == "Job 5"

    def test_search_is_case_insensitive(self) -> None:
        items = _make_items(10)
        result = apply_search(items, "JOB 3", ["title"])
        assert any(i.title == "Job 3" for i in result)

    def test_search_across_multiple_fields(self) -> None:
        items = _make_items(10)
        # title "Job 1" or company "Company 1"
        result = apply_search(items, "company 0", ["title", "company"])
        assert all(i.company == "Company 0" for i in result)

    def test_empty_search_returns_all(self) -> None:
        items = _make_items(10)
        assert apply_search(items, None, ["title"]) == items
        assert apply_search(items, "  ", ["title"]) == items

    def test_no_match_returns_empty(self) -> None:
        items = _make_items(5)
        assert apply_search(items, "xxxxxxxxxx", ["title", "company"]) == []

    def test_partial_match(self) -> None:
        items = _make_items(20)
        result = apply_search(items, "Job 1", ["title"])
        # Matches "Job 1", "Job 10", "Job 11" ... "Job 19"
        assert len(result) >= 1
        assert all("job 1" in i.title.lower() for i in result)


# ---------------------------------------------------------------------------
# Filter tests
# ---------------------------------------------------------------------------


class TestFilters:
    def test_filter_by_source(self) -> None:
        items = _make_items(9)
        result = apply_filters(items, {"source": "rocketjobs"})
        assert all(i.source == "rocketjobs" for i in result)
        assert len(result) > 0

    def test_filter_by_status(self) -> None:
        items = _make_items(9)
        result = apply_filters(items, {"status": "sent"})
        assert all(i.status == "sent" for i in result)

    def test_all_sentinel_skips_filter(self) -> None:
        items = _make_items(9)
        assert apply_filters(items, {"source": "(all)"}) == items
        assert apply_filters(items, {"source": None}) == items
        assert apply_filters(items, {"source": ""}) == items

    def test_empty_filters_returns_all(self) -> None:
        items = _make_items(5)
        assert apply_filters(items, {}) == items

    def test_multiple_filters_combined(self) -> None:
        items = _make_items(30)
        result = apply_filters(items, {"source": "rocketjobs", "status": "new"})
        for item in result:
            assert item.source == "rocketjobs"
            assert item.status == "new"


# ---------------------------------------------------------------------------
# Sort tests
# ---------------------------------------------------------------------------


class TestSort:
    def test_sort_by_score_desc(self) -> None:
        items = [_Item(id=i, title="T", company="C", score=i * 5) for i in range(5, 0, -1)]
        result = apply_sort(items, "score", "desc")
        scores = [i.score for i in result if i.score is not None]
        assert scores == sorted(scores, reverse=True)

    def test_sort_by_score_asc(self) -> None:
        items = [_Item(id=i, title="T", company="C", score=i * 5) for i in range(5, 0, -1)]
        result = apply_sort(items, "score", "asc")
        scores = [i.score for i in result if i.score is not None]
        assert scores == sorted(scores)

    def test_sort_by_string_field(self) -> None:
        items = [
            _Item(id=1, title="Zebra", company="Z"),
            _Item(id=2, title="Apple", company="A"),
            _Item(id=3, title="Mango", company="M"),
        ]
        result = apply_sort(items, "title", "asc")
        assert [i.title for i in result] == ["Apple", "Mango", "Zebra"]

    def test_none_values_sort_to_end(self) -> None:
        items = [
            _Item(id=1, title="A", company="C", score=50),
            _Item(id=2, title="B", company="C", score=None),
            _Item(id=3, title="C", company="C", score=80),
        ]
        result_asc = apply_sort(items, "score", "asc")
        result_desc = apply_sort(items, "score", "desc")
        # None should be last in both cases
        assert result_asc[-1].score is None
        assert result_desc[-1].score is None

    def test_sort_no_crash_on_missing_attribute(self) -> None:
        @dataclass
        class _Sparse:
            id: int
            title: str

        items = [_Sparse(id=1, title="B"), _Sparse(id=2, title="A")]
        result = apply_sort(items, "score", "asc")  # "score" doesn't exist on _Sparse
        assert len(result) == 2

    def test_no_sort_by_returns_original_order(self) -> None:
        items = _make_items(5)
        original_ids = [i.id for i in items]
        result = apply_sort(items, None, "asc")
        assert [i.id for i in result] == original_ids


# ---------------------------------------------------------------------------
# build_list_result integration tests
# ---------------------------------------------------------------------------


class TestBuildListResult:
    def test_pipeline_search_filter_sort_paginate(self) -> None:
        items = _make_items(50)
        query = ListQuery(
            page=1,
            page_size=5,
            search_text="company 0",
            sort_by="score",
            sort_dir="desc",
            filters={"source": "rocketjobs"},
        )
        result = build_list_result(items, query, search_fields=["title", "company"])
        assert len(result.items) <= 5
        for item in result.items:
            assert "company 0" in item.company.lower()
            assert item.source == "rocketjobs"

    def test_filter_fn_applied_before_search(self) -> None:
        items = _make_items(30)
        query = ListQuery(page=1, page_size=100)
        # Only keep items where id is even
        result = build_list_result(items, query, filter_fn=lambda i: i.id % 2 == 0)
        assert all(i.id % 2 == 0 for i in result.items)

    def test_offers_list_returns_only_current_page(self) -> None:
        """Simulate the Offers page: 100 offers, page_size=25, check page 2."""
        items = _make_items(100)
        query = ListQuery(page=2, page_size=25)
        result = build_list_result(items, query)
        assert len(result.items) == 25
        assert result.start_index == 26
        assert result.end_index == 50

    def test_applications_list_returns_only_current_page(self) -> None:
        """Simulate the Applications page: 80 apps, page_size=25, last page."""
        items = _make_items(80)
        query = ListQuery(page=4, page_size=25)
        result = build_list_result(items, query)
        assert len(result.items) == 5
        assert result.start_index == 76
        assert result.end_index == 80

    def test_changing_filter_should_produce_different_result(self) -> None:
        items = _make_items(30)
        q_all = ListQuery(page=1, page_size=100, filters={})
        q_filtered = ListQuery(page=1, page_size=100, filters={"source": "rocketjobs"})
        r_all = build_list_result(items, q_all)
        r_filtered = build_list_result(items, q_filtered)
        assert r_filtered.total_count < r_all.total_count

    def test_dict_items_supported(self) -> None:
        """listing helpers also work on plain dicts (collector result rows)."""
        items = [{"id": i, "title": f"Job {i}", "source": "rocketjobs"} for i in range(20)]
        query = ListQuery(page=1, page_size=5)
        result = build_list_result(items, query, search_fields=["title"])
        assert len(result.items) == 5
        assert result.total_count == 20
