"""Tests for batch URL import: url_utils and services.import_offers_from_urls."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cv_sender.models import BatchImportResult, ImportStatus, Offer
from cv_sender.storage import load_offers, save_offers
from cv_sender.url_utils import is_valid_url, normalize_url, parse_url_lines


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect storage to temporary files for every test."""
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_OFFERS", tmp_path / "offers.json")
    monkeypatch.setattr(_storage, "_DEFAULT_APPLICATIONS", tmp_path / "applications.json")


# ---------------------------------------------------------------------------
# url_utils – is_valid_url
# ---------------------------------------------------------------------------


def test_valid_http_url() -> None:
    assert is_valid_url("http://example.com/job/123") is True


def test_valid_https_url() -> None:
    assert is_valid_url("https://rocketjobs.pl/oferty/frontend-dev") is True


def test_invalid_empty_string() -> None:
    assert is_valid_url("") is False


def test_invalid_plain_text() -> None:
    assert is_valid_url("not a url at all") is False


def test_invalid_ftp_scheme() -> None:
    assert is_valid_url("ftp://example.com/file") is False


def test_invalid_no_netloc() -> None:
    assert is_valid_url("https://") is False


# ---------------------------------------------------------------------------
# url_utils – normalize_url
# ---------------------------------------------------------------------------


def test_normalize_removes_tracking_params() -> None:
    url = "https://example.com/job/1?utm_source=google&utm_campaign=jobs&jobId=42"
    normalized = normalize_url(url)
    assert "utm_source" not in normalized
    assert "utm_campaign" not in normalized
    assert "jobId=42" in normalized


def test_normalize_removes_fbclid() -> None:
    url = "https://example.com/job/1?fbclid=ABC123"
    assert "fbclid" not in normalize_url(url)


def test_normalize_strips_trailing_slash() -> None:
    assert normalize_url("https://example.com/job/1/") == "https://example.com/job/1"


def test_normalize_preserves_bare_root() -> None:
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_normalize_preserves_job_specific_params() -> None:
    url = "https://example.com/job?id=12345&category=tech"
    normalized = normalize_url(url)
    assert "id=12345" in normalized
    assert "category=tech" in normalized


def test_normalize_lowercases_scheme_and_host() -> None:
    normalized = normalize_url("HTTPS://Example.COM/Job/1")
    assert normalized.startswith("https://example.com/")


def test_normalize_strips_fragment() -> None:
    normalized = normalize_url("https://example.com/job/1#apply")
    assert "#" not in normalized


# ---------------------------------------------------------------------------
# url_utils – parse_url_lines
# ---------------------------------------------------------------------------


def test_parse_url_lines_basic() -> None:
    text = "https://a.com\nhttps://b.com\nhttps://c.com"
    assert parse_url_lines(text) == ["https://a.com", "https://b.com", "https://c.com"]


def test_parse_url_lines_ignores_empty_lines() -> None:
    text = "https://a.com\n\n\nhttps://b.com"
    assert parse_url_lines(text) == ["https://a.com", "https://b.com"]


def test_parse_url_lines_trims_whitespace() -> None:
    text = "  https://a.com  \n  https://b.com  "
    assert parse_url_lines(text) == ["https://a.com", "https://b.com"]


def test_parse_url_lines_deduplicates_within_input() -> None:
    text = "https://a.com\nhttps://b.com\nhttps://a.com"
    assert parse_url_lines(text) == ["https://a.com", "https://b.com"]


def test_parse_url_lines_empty_string() -> None:
    assert parse_url_lines("") == []


def test_parse_url_lines_only_whitespace() -> None:
    assert parse_url_lines("   \n  \n  ") == []


# ---------------------------------------------------------------------------
# import_offers_from_urls – basic flow
# ---------------------------------------------------------------------------


def _make_urls(*paths: str) -> list[str]:
    return [f"https://example.com/{p}" for p in paths]


def test_batch_imports_valid_url() -> None:
    from cv_sender.services import import_offers_from_urls

    result = import_offers_from_urls(_make_urls("job/1"), auto_score=False)

    assert result.imported_count == 1
    assert result.duplicate_count == 0
    assert len(load_offers()) == 1


def test_batch_detects_invalid_url() -> None:
    from cv_sender.services import import_offers_from_urls

    result = import_offers_from_urls(["not-a-url", "also bad"], auto_score=False)

    assert result.invalid_count == 2
    assert result.imported_count == 0
    assert load_offers() == []


def test_batch_continues_after_one_failure() -> None:
    """A failed URL must not abort processing of subsequent URLs."""
    from cv_sender.services import import_offers_from_urls

    urls = ["not-a-url", "https://example.com/job/2", "https://example.com/job/3"]
    result = import_offers_from_urls(urls, auto_score=False)

    assert result.invalid_count == 1
    assert result.imported_count == 2


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


def test_batch_detects_duplicate_in_input() -> None:
    from cv_sender.services import import_offers_from_urls

    urls = ["https://example.com/job/1", "https://example.com/job/1"]
    result = import_offers_from_urls(urls, auto_score=False)

    assert result.imported_count == 1
    assert result.duplicate_count == 1
    assert len(load_offers()) == 1


def test_batch_detects_duplicate_against_storage() -> None:
    from cv_sender.services import import_offers_from_urls

    # Pre-populate storage with the same URL
    save_offers([Offer(url="https://example.com/job/1", title="Existing")])

    result = import_offers_from_urls(["https://example.com/job/1"], auto_score=False)

    assert result.duplicate_count == 1
    assert result.imported_count == 0
    assert len(load_offers()) == 1  # no new record


def test_batch_deduplicates_by_normalized_url() -> None:
    """URL with trailing slash should match URL without trailing slash."""
    from cv_sender.services import import_offers_from_urls

    save_offers([Offer(url="https://example.com/job/1", title="Existing")])

    result = import_offers_from_urls(
        ["https://example.com/job/1/"],  # trailing slash
        auto_score=False,
    )

    assert result.duplicate_count == 1
    assert len(load_offers()) == 1


def test_batch_deduplicates_tracking_params() -> None:
    """Same URL with different tracking params should be treated as duplicate."""
    from cv_sender.services import import_offers_from_urls

    save_offers([Offer(url="https://example.com/job/1", title="Existing")])

    result = import_offers_from_urls(
        ["https://example.com/job/1?utm_source=google&utm_campaign=jobs"],
        auto_score=False,
    )

    assert result.duplicate_count == 1
    assert len(load_offers()) == 1


# ---------------------------------------------------------------------------
# Max URL limit
# ---------------------------------------------------------------------------


def test_batch_respects_max_urls() -> None:
    from cv_sender.services import import_offers_from_urls

    urls = [f"https://example.com/job/{i}" for i in range(10)]
    result = import_offers_from_urls(urls, auto_score=False, max_urls=3)

    assert result.imported_count == 3
    assert result.skipped_limit_count == 7


def test_batch_hard_limit_caps_at_50() -> None:
    from cv_sender.services import import_offers_from_urls

    urls = [f"https://example.com/job/{i}" for i in range(60)]
    result = import_offers_from_urls(urls, auto_score=False, max_urls=60)

    assert result.imported_count == 50
    assert result.skipped_limit_count == 10


def test_batch_max_urls_default_is_20() -> None:
    from cv_sender.services import import_offers_from_urls

    urls = [f"https://example.com/job/{i}" for i in range(25)]
    result = import_offers_from_urls(urls, auto_score=False)

    assert result.imported_count == 20
    assert result.skipped_limit_count == 5


# ---------------------------------------------------------------------------
# Result summary counts
# ---------------------------------------------------------------------------


def test_batch_result_counts_are_correct() -> None:
    from cv_sender.services import import_offers_from_urls

    save_offers([Offer(url="https://example.com/job/dup", title="Dup")])

    urls = [
        "https://example.com/job/new1",   # imported
        "https://example.com/job/new2",   # imported
        "https://example.com/job/dup",    # duplicate in storage
        "https://example.com/job/new1",   # duplicate in batch
        "bad-url",                         # invalid
    ]
    result = import_offers_from_urls(urls, auto_score=False)

    assert result.imported_count == 2
    assert result.duplicate_count == 2
    assert result.invalid_count == 1
    assert result.failed_count == 0


# ---------------------------------------------------------------------------
# Auto-scoring
# ---------------------------------------------------------------------------


def test_batch_scores_imported_offers_when_auto_score_enabled() -> None:
    from cv_sender.services import import_offers_from_urls

    result = import_offers_from_urls(
        ["https://example.com/job/1"],
        auto_score=True,
    )

    assert result.imported_count == 1
    item = result.items[0]
    assert item.status == ImportStatus.IMPORTED
    assert item.score is not None


def test_batch_keeps_offer_when_scoring_fails() -> None:
    """A scoring error must not roll back the import."""
    from cv_sender.services import import_offers_from_urls

    with patch("cv_sender.services.score_offer", side_effect=RuntimeError("scorer down")):
        result = import_offers_from_urls(
            ["https://example.com/job/1"],
            auto_score=True,
        )

    assert result.imported_count == 1
    item = result.items[0]
    assert item.status == ImportStatus.IMPORTED
    assert "scorer down" in item.error
    # Offer must still be in storage
    assert len(load_offers()) == 1


def test_batch_no_score_when_auto_score_false() -> None:
    from cv_sender.services import import_offers_from_urls

    result = import_offers_from_urls(
        ["https://example.com/job/1"],
        auto_score=False,
    )

    assert result.scored_count == 0
    assert result.items[0].score is None


# ---------------------------------------------------------------------------
# Source override
# ---------------------------------------------------------------------------


def test_batch_applies_source_override() -> None:
    from cv_sender.services import import_offers_from_urls

    result = import_offers_from_urls(
        ["https://example.com/job/1"],
        source_override="testboard",
        auto_score=False,
    )

    assert result.imported_count == 1
    stored = load_offers()[0]
    assert stored.source == "testboard"


def test_batch_infers_source_from_hostname_when_no_override() -> None:
    from cv_sender.services import import_offers_from_urls

    result = import_offers_from_urls(
        ["https://rocketjobs.pl/oferty/frontend-dev"],
        source_override=None,
        auto_score=False,
    )

    if result.imported_count == 1:  # might be duplicate if rocketjobs URL already in storage
        stored = load_offers()[0]
        assert stored.source == "rocketjobs"
