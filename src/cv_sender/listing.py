"""Reusable pagination, filtering and sorting utilities for large list views.

Design rules:
- Pure Python / Pydantic only — no Streamlit imports.
- All functions are stateless; callers own session_state.
- None-safe: missing fields sort as lowest value (None < any real value).
- Streamlit UI helpers live in a separate section at the bottom of this file
  so they can be imported without importing all of Streamlit.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

PAGE_SIZE_OPTIONS: list[int] = [10, 25, 50, 100]
DEFAULT_PAGE_SIZE: int = 25
DEFAULT_PAGE_SIZE_SMALL: int = 10

SortDir = Literal["asc", "desc"]


# ---------------------------------------------------------------------------
# Query & Result models
# ---------------------------------------------------------------------------


class ListQuery(BaseModel):
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    search_text: str | None = None
    sort_by: str | None = None
    sort_dir: SortDir = "desc"
    filters: dict[str, Any] = Field(default_factory=dict)


class ListResult(BaseModel, arbitrary_types_allowed=True):
    items: list[Any]
    total_count: int
    page: int
    page_size: int
    total_pages: int
    has_prev: bool
    has_next: bool
    start_index: int  # 1-based
    end_index: int    # 1-based

    @classmethod
    def build(cls, items: list[Any], all_items: list[Any], query: ListQuery) -> "ListResult":
        total = len(all_items)
        page_size = max(1, query.page_size)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(query.page, total_pages))
        start = (page - 1) * page_size
        end = start + page_size
        page_items = items[start:end]
        return cls(
            items=page_items,
            total_count=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            has_prev=page > 1,
            has_next=page < total_pages,
            start_index=start + 1 if total else 0,
            end_index=min(end, total),
        )


# ---------------------------------------------------------------------------
# Core list operations
# ---------------------------------------------------------------------------


def _get_attr(item: Any, field: str) -> Any:
    """Safely get nested attribute or dict key."""
    if isinstance(item, dict):
        return item.get(field)
    return getattr(item, field, None)


def _norm_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).lower()


def apply_search(items: list[T], search_text: str | None, fields: list[str]) -> list[T]:
    """Keep items where *any* of the given fields contains the search string (case-insensitive)."""
    if not search_text or not search_text.strip():
        return items
    needle = search_text.strip().lower()
    # Support quoted phrases and bare words
    result: list[T] = []
    for item in items:
        for field in fields:
            if needle in _norm_str(_get_attr(item, field)):
                result.append(item)
                break
    return result


def apply_filters(items: list[T], filters: dict[str, Any]) -> list[T]:
    """Apply key=value equality filters.

    Special sentinel ``None`` and ``""`` and ``"(all)"`` are all treated as
    "no filter" so callers don't have to pre-clean selectbox values.
    """
    if not filters:
        return items
    result = items
    for field, value in filters.items():
        if value is None or value == "" or value == "(all)":
            continue
        result = [item for item in result if _norm_str(_get_attr(item, field)) == _norm_str(value)]
    return result


def apply_sort(items: list[T], sort_by: str | None, sort_dir: SortDir = "desc") -> list[T]:
    """Sort items by a field name.  Items where the field is ``None`` are always
    placed at the **end** of the result regardless of sort direction."""
    if not sort_by or not items:
        return items

    none_items: list[T] = []
    value_items: list[T] = []
    for item in items:
        if _get_attr(item, sort_by) is None:
            none_items.append(item)
        else:
            value_items.append(item)

    def _key(item: Any) -> Any:
        v = _get_attr(item, sort_by)
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, (int, float)):
            return v
        return _norm_str(v)

    sorted_values = sorted(value_items, key=_key, reverse=(sort_dir == "desc"))
    return sorted_values + none_items


def apply_filter_fn(items: list[T], filter_fn: Callable[[T], bool] | None) -> list[T]:
    """Apply a custom predicate function."""
    if filter_fn is None:
        return items
    return [item for item in items if filter_fn(item)]


def build_list_result(
    items: list[T],
    query: ListQuery,
    search_fields: list[str] | None = None,
    filter_fn: Callable[[T], bool] | None = None,
) -> ListResult:
    """Full pipeline: custom_filter → search → field_filters → sort → paginate."""
    filtered = apply_filter_fn(items, filter_fn)
    filtered = apply_search(filtered, query.search_text, search_fields or [])
    filtered = apply_filters(filtered, query.filters)
    filtered = apply_sort(filtered, query.sort_by, query.sort_dir)
    return ListResult.build(filtered, filtered, query)


# ---------------------------------------------------------------------------
# Streamlit UI helpers  (only imported when Streamlit is available)
# ---------------------------------------------------------------------------


def render_search_box(key_prefix: str, label: str = "Search", placeholder: str = "") -> str | None:
    """Render a search text input and return the current value."""
    import streamlit as st  # noqa: PLC0415

    return st.text_input(label, key=f"{key_prefix}_search", placeholder=placeholder) or None


def render_page_size_selector(key_prefix: str, default: int = DEFAULT_PAGE_SIZE) -> int:
    """Render a page-size selectbox and return the selected value."""
    import streamlit as st  # noqa: PLC0415

    options = PAGE_SIZE_OPTIONS
    idx = options.index(default) if default in options else 0
    return st.selectbox("Per page", options, index=idx, key=f"{key_prefix}_page_size")  # type: ignore[return-value]


def render_sort_controls(
    key_prefix: str,
    sort_options: list[str],
    default_sort: str | None = None,
    default_dir: SortDir = "desc",
) -> tuple[str | None, SortDir]:
    """Render sort-by + direction controls and return (sort_by, sort_dir)."""
    import streamlit as st  # noqa: PLC0415

    col1, col2 = st.columns(2)
    idx = sort_options.index(default_sort) if default_sort and default_sort in sort_options else 0
    sort_by: str = col1.selectbox("Sort by", sort_options, index=idx, key=f"{key_prefix}_sort_by")  # type: ignore[assignment]
    dir_label: str = col2.selectbox("Direction", ["Descending", "Ascending"], key=f"{key_prefix}_sort_dir")  # type: ignore[assignment]
    sort_dir: SortDir = "asc" if dir_label == "Ascending" else "desc"
    return sort_by, sort_dir


def render_pagination_controls(key_prefix: str, result: ListResult) -> int:
    """Render Prev/Next/page-selector controls.

    Returns the newly selected page number (caller must store in session_state).
    """
    import streamlit as st  # noqa: PLC0415

    if result.total_pages <= 1 and result.total_count == 0:
        return 1

    col_info, col_prev, col_page, col_next = st.columns([3, 1, 2, 1])

    col_info.caption(
        f"Showing **{result.start_index}–{result.end_index}** of **{result.total_count}** "
        f"(page {result.page}/{result.total_pages})"
    )

    new_page = result.page

    if col_prev.button("◀ Prev", key=f"{key_prefix}_prev", disabled=not result.has_prev):
        new_page = result.page - 1

    if result.total_pages > 1:
        pages = list(range(1, result.total_pages + 1))
        idx = result.page - 1
        chosen = col_page.selectbox(
            "Page",
            pages,
            index=idx,
            key=f"{key_prefix}_page_sel",
            label_visibility="collapsed",
        )
        if chosen != result.page:
            new_page = int(chosen)  # type: ignore[arg-type]

    if col_next.button("Next ▶", key=f"{key_prefix}_next", disabled=not result.has_next):
        new_page = result.page + 1

    return new_page


def init_list_state(key_prefix: str, defaults: dict[str, Any] | None = None) -> None:
    """Ensure all session_state keys for a list view exist with sensible defaults."""
    import streamlit as st  # noqa: PLC0415

    base: dict[str, Any] = {
        f"{key_prefix}_page": 1,
        f"{key_prefix}_page_size": DEFAULT_PAGE_SIZE,
        f"{key_prefix}_sort_by": None,
        f"{key_prefix}_sort_dir": "desc",
        f"{key_prefix}_search": None,
        f"{key_prefix}_filters": {},
    }
    if defaults:
        for k, v in defaults.items():
            base[f"{key_prefix}_{k}"] = v
    for k, v in base.items():
        if k not in st.session_state:
            st.session_state[k] = v


def reset_page_on_filter_change(key_prefix: str, *watched_keys: str) -> None:
    """Reset page to 1 when any watched filter key value differs from its last-seen value."""
    import streamlit as st  # noqa: PLC0415

    sentinel_key = f"{key_prefix}_filter_sentinel"
    current = {k: st.session_state.get(k) for k in watched_keys}
    previous = st.session_state.get(sentinel_key, {})
    if current != previous:
        st.session_state[f"{key_prefix}_page"] = 1
    st.session_state[sentinel_key] = current


def _read_query(key_prefix: str) -> ListQuery:
    """Build a ListQuery from session_state keys for a given prefix."""
    import streamlit as st  # noqa: PLC0415

    return ListQuery(
        page=st.session_state.get(f"{key_prefix}_page", 1),
        page_size=st.session_state.get(f"{key_prefix}_page_size", DEFAULT_PAGE_SIZE),
        search_text=st.session_state.get(f"{key_prefix}_search") or None,
        sort_by=st.session_state.get(f"{key_prefix}_sort_by") or None,
        sort_dir=st.session_state.get(f"{key_prefix}_sort_dir", "desc"),
        filters=st.session_state.get(f"{key_prefix}_filters", {}),
    )
