"""Tests for the bookmarklet HTTP server.

All calls to ``services.import_offer_from_url`` are mocked – no real HTTP
requests or file I/O are performed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from cv_sender.bookmarklet_server import BOOKMARKLET_JS, app
from cv_sender.models import BatchImportItemResult, Decision, ImportStatus

# ---------------------------------------------------------------------------
# Test client
# ---------------------------------------------------------------------------

client = TestClient(app, raise_server_exceptions=True)

# ---------------------------------------------------------------------------
# Bookmarklet JS format
# ---------------------------------------------------------------------------


def test_bookmarklet_js_starts_with_javascript_protocol() -> None:
    assert BOOKMARKLET_JS.startswith("javascript:")


def test_bookmarklet_js_contains_localhost_8765() -> None:
    assert "localhost:8765" in BOOKMARKLET_JS


def test_bookmarklet_js_references_import_endpoint() -> None:
    assert "/import?url=" in BOOKMARKLET_JS


def test_bookmarklet_js_uses_encode_uri_component() -> None:
    assert "encodeURIComponent" in BOOKMARKLET_JS


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def test_health_endpoint_returns_ok() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /import – missing url parameter
# ---------------------------------------------------------------------------


def test_import_missing_url_param_returns_422() -> None:
    resp = client.get("/import")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /import – invalid URL (rejected before calling service)
# ---------------------------------------------------------------------------


def test_import_invalid_url_returns_html_error() -> None:
    resp = client.get("/import", params={"url": "not-a-url"})
    assert resp.status_code == 200
    assert "Invalid URL" in resp.text
    assert "error" in resp.text


def test_import_plain_text_url_returns_html_error() -> None:
    resp = client.get("/import", params={"url": "ftp://example.com/job"})
    assert resp.status_code == 200
    assert "Invalid URL" in resp.text


# ---------------------------------------------------------------------------
# /import – success (ImportStatus.IMPORTED)
# ---------------------------------------------------------------------------


@pytest.fixture()
def imported_result() -> BatchImportItemResult:
    return BatchImportItemResult(
        url="https://rocketjobs.pl/oferty/senior-frontend-123",
        status=ImportStatus.IMPORTED,
        offer_id="aabbccdd-1122-3344-5566-778899aabbcc",
        title="senior frontend developer",
        company="ACME Corp",
        score=80,
        decision=Decision.APPLY,
    )


def test_import_success_returns_html_with_success_card(imported_result: BatchImportItemResult) -> None:
    with patch("cv_sender.services.import_offer_from_url", return_value=imported_result):
        resp = client.get(
            "/import",
            params={"url": "https://rocketjobs.pl/oferty/senior-frontend-123"},
        )
    assert resp.status_code == 200
    assert "Offer saved" in resp.text
    assert "success" in resp.text


def test_import_success_shows_title(imported_result: BatchImportItemResult) -> None:
    with patch("cv_sender.services.import_offer_from_url", return_value=imported_result):
        resp = client.get(
            "/import",
            params={"url": "https://rocketjobs.pl/oferty/senior-frontend-123"},
        )
    assert "senior frontend developer" in resp.text


def test_import_success_shows_score(imported_result: BatchImportItemResult) -> None:
    with patch("cv_sender.services.import_offer_from_url", return_value=imported_result):
        resp = client.get(
            "/import",
            params={"url": "https://rocketjobs.pl/oferty/senior-frontend-123"},
        )
    assert "80" in resp.text


def test_import_success_shows_streamlit_link(imported_result: BatchImportItemResult) -> None:
    with patch("cv_sender.services.import_offer_from_url", return_value=imported_result):
        resp = client.get(
            "/import",
            params={"url": "https://rocketjobs.pl/oferty/senior-frontend-123"},
        )
    assert "localhost:8501" in resp.text


# ---------------------------------------------------------------------------
# /import – duplicate
# ---------------------------------------------------------------------------


@pytest.fixture()
def duplicate_result() -> BatchImportItemResult:
    return BatchImportItemResult(
        url="https://pracuj.pl/praca/dev-456",
        status=ImportStatus.DUPLICATE,
        offer_id="deadbeef-0000-0000-0000-000000000000",
        title="Backend Developer",
        company="TechCo",
        score=65,
        decision=Decision.MAYBE,
    )


def test_import_duplicate_returns_html_with_duplicate_card(duplicate_result: BatchImportItemResult) -> None:
    with patch("cv_sender.services.import_offer_from_url", return_value=duplicate_result):
        resp = client.get("/import", params={"url": "https://pracuj.pl/praca/dev-456"})
    assert resp.status_code == 200
    assert "Already in database" in resp.text
    assert "duplicate" in resp.text


def test_import_duplicate_shows_existing_offer_details(duplicate_result: BatchImportItemResult) -> None:
    with patch("cv_sender.services.import_offer_from_url", return_value=duplicate_result):
        resp = client.get("/import", params={"url": "https://pracuj.pl/praca/dev-456"})
    assert "Backend Developer" in resp.text
    assert "TechCo" in resp.text


# ---------------------------------------------------------------------------
# /import – failed
# ---------------------------------------------------------------------------


@pytest.fixture()
def failed_result() -> BatchImportItemResult:
    return BatchImportItemResult(
        url="https://example.com/job/999",
        status=ImportStatus.FAILED,
        error="Storage write error",
    )


def test_import_failed_returns_html_with_error_card(failed_result: BatchImportItemResult) -> None:
    with patch("cv_sender.services.import_offer_from_url", return_value=failed_result):
        resp = client.get("/import", params={"url": "https://example.com/job/999"})
    assert resp.status_code == 200
    assert "Import failed" in resp.text
    assert "error" in resp.text


def test_import_failed_shows_error_message(failed_result: BatchImportItemResult) -> None:
    with patch("cv_sender.services.import_offer_from_url", return_value=failed_result):
        resp = client.get("/import", params={"url": "https://example.com/job/999"})
    assert "Storage write error" in resp.text


# ---------------------------------------------------------------------------
# /import – service raises unexpected exception (should not crash server)
# ---------------------------------------------------------------------------


def test_import_does_not_crash_on_service_exception() -> None:
    with patch("cv_sender.services.import_offer_from_url", side_effect=RuntimeError("boom")):
        resp = client.get("/import", params={"url": "https://example.com/job/boom"})
    assert resp.status_code == 200
    assert "Import failed" in resp.text
    assert "boom" in resp.text
