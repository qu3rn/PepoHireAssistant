"""Tests for Playwright-based job collectors.

Uses mock Playwright Page objects — does NOT hit real websites.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cv_sender.collectors.base import JobSearchCriteria
from cv_sender.collectors.playwright_base import (
    PlaywrightCollectionResult,
    PlaywrightJobCollector,
    classify_page,
    detect_blocked_page,
    detect_captcha,
    detect_login_wall,
)
from cv_sender.collectors.playwright_justjoin import PlaywrightJustJoinCollector
from cv_sender.collectors.playwright_nofluffjobs import PlaywrightNoFluffJobsCollector
from cv_sender.collectors.playwright_pracuj import PlaywrightPracujCollector
from cv_sender.collectors.playwright_rocketjobs import PlaywrightRocketJobsCollector


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def broad_criteria() -> JobSearchCriteria:
    return JobSearchCriteria(
        keywords=["React", "Frontend"],
        technologies=["React", "TypeScript"],
        locations=[],
        seniority=[],
        contract_types=[],
        min_salary_b2b=0,
        require_salary=False,
        max_offers_per_source=20,
        max_total_offers=100,
        exclude_keywords=[],
        request_delay_seconds=0.0,
    )


@pytest.fixture
def minimal_cfg():
    """Minimal Playwright config object (not a full PlaywrightCollectionConfig)."""

    class _Cfg:
        headless = True
        slow_mo_ms = 0
        max_scrolls_per_source = 3
        scroll_pause_ms = 0
        max_urls_per_source = 10
        page_timeout_ms = 10_000
        save_debug_screenshots = False
        save_debug_html_preview = False
        user_agent = None

    return _Cfg()


# ---------------------------------------------------------------------------
# detect_* helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Please complete the CAPTCHA to continue.",
        "Verify you are human",
        "Just a moment…",
        "Checking your browser before accessing",
        "I am not a robot",
        "Enable JavaScript and cookies to continue",
    ],
)
def test_detect_captcha_positive(text: str) -> None:
    assert detect_captcha(text) is True


def test_detect_captcha_negative() -> None:
    assert detect_captcha("Here are 10 React jobs for you.") is False


@pytest.mark.parametrize(
    "text",
    [
        "Access Denied. You don't have permission.",
        "403 Forbidden",
        "Your IP has been blocked.",
    ],
)
def test_detect_blocked_positive(text: str) -> None:
    assert detect_blocked_page(text) is True


def test_detect_blocked_negative() -> None:
    assert detect_blocked_page("20 job offers found for React developers.") is False


@pytest.mark.parametrize(
    "text",
    [
        "Sign in to view more jobs",
        "Please login to continue",
        "Authentication required",
    ],
)
def test_detect_login_wall_positive(text: str) -> None:
    assert detect_login_wall(text) is True


def test_detect_login_wall_negative() -> None:
    assert detect_login_wall("100 offers available") is False


def test_classify_page_captcha() -> None:
    assert classify_page("verify you are human") == "captcha"


def test_classify_page_blocked() -> None:
    assert classify_page("Access Denied") == "blocked"


def test_classify_page_login_wall() -> None:
    assert classify_page("Please sign in to view results") == "login_wall"


def test_classify_page_normal() -> None:
    assert classify_page("10 React jobs in Warsaw") is None


# ---------------------------------------------------------------------------
# URL normalization — JustJoin
# ---------------------------------------------------------------------------


def test_justjoin_is_job_url_positive() -> None:
    c = PlaywrightJustJoinCollector()
    assert c.is_job_url("https://justjoin.it/offers/senior-react-developer-warsaw")
    assert c.is_job_url("https://justjoin.it/job-offers/react-dev-at-acme")
    assert c.is_job_url("https://www.justjoin.it/offers/frontend-dev")


def test_justjoin_is_job_url_negative() -> None:
    c = PlaywrightJustJoinCollector()
    assert not c.is_job_url("https://justjoin.it/job-offers")  # listing page, no trailing slug
    assert not c.is_job_url("https://linkedin.com/jobs/view/123")
    assert not c.is_job_url("https://justjoin.it/companies/acme")


def test_justjoin_normalize_strips_query() -> None:
    c = PlaywrightJustJoinCollector()
    url = "https://justjoin.it/offers/react-dev?utm_source=google&utm_medium=cpc"
    assert c.normalize_job_url(url) == "https://justjoin.it/offers/react-dev"


def test_justjoin_build_search_urls(broad_criteria: JobSearchCriteria) -> None:
    c = PlaywrightJustJoinCollector()
    urls = c.build_search_urls(broad_criteria)
    assert len(urls) >= 1
    assert all("justjoin.it" in u for u in urls)


def test_justjoin_build_search_urls_empty_keywords() -> None:
    c = PlaywrightJustJoinCollector()
    criteria = JobSearchCriteria(
        keywords=[],
        technologies=[],
        locations=[],
        seniority=[],
        contract_types=[],
        min_salary_b2b=0,
        require_salary=False,
        max_offers_per_source=10,
        max_total_offers=50,
        exclude_keywords=[],
    )
    urls = c.build_search_urls(criteria)
    assert urls == ["https://justjoin.it/job-offers"]


# ---------------------------------------------------------------------------
# URL normalization — RocketJobs
# ---------------------------------------------------------------------------


def test_rocketjobs_is_job_url_positive() -> None:
    c = PlaywrightRocketJobsCollector()
    assert c.is_job_url("https://rocketjobs.pl/oferty-pracy/senior-react-dev")
    assert c.is_job_url("https://www.rocketjobs.pl/oferty-pracy/frontend-engineer")


def test_rocketjobs_is_job_url_negative() -> None:
    c = PlaywrightRocketJobsCollector()
    assert not c.is_job_url("https://rocketjobs.pl/oferty-pracy")  # listing, no slug
    assert not c.is_job_url("https://rocketjobs.pl/firmy/acme")
    assert not c.is_job_url("https://justjoin.it/offers/frontend")


def test_rocketjobs_normalize_strips_query() -> None:
    c = PlaywrightRocketJobsCollector()
    url = "https://rocketjobs.pl/oferty-pracy/react-dev?ref=homepage"
    assert c.normalize_job_url(url) == "https://rocketjobs.pl/oferty-pracy/react-dev"


# ---------------------------------------------------------------------------
# URL normalization — NoFluffJobs
# ---------------------------------------------------------------------------


def test_nofluffjobs_is_job_url_positive() -> None:
    c = PlaywrightNoFluffJobsCollector()
    assert c.is_job_url("https://nofluffjobs.com/job/react-developer-acme-warsaw")
    assert c.is_job_url("https://nofluffjobs.com/pl/job/frontend-dev-at-company")


def test_nofluffjobs_is_job_url_negative() -> None:
    c = PlaywrightNoFluffJobsCollector()
    assert not c.is_job_url("https://nofluffjobs.com/react")
    assert not c.is_job_url("https://justjoin.it/job/react-dev")


def test_nofluffjobs_normalize_removes_region_prefix() -> None:
    c = PlaywrightNoFluffJobsCollector()
    url_pl = "https://nofluffjobs.com/pl/job/react-dev-acme"
    url_en = "https://nofluffjobs.com/job/react-dev-acme"
    assert c.normalize_job_url(url_pl) == c.normalize_job_url(url_en)


def test_nofluffjobs_normalize_strips_trailing_slash() -> None:
    c = PlaywrightNoFluffJobsCollector()
    url = "https://nofluffjobs.com/job/react-dev-at-acme/"
    assert not c.normalize_job_url(url).endswith("/")


# ---------------------------------------------------------------------------
# URL normalization — Pracuj
# ---------------------------------------------------------------------------


def test_pracuj_is_job_url_positive() -> None:
    c = PlaywrightPracujCollector()
    assert c.is_job_url("https://www.pracuj.pl/praca/react-developer,oferta,1234567890")
    assert c.is_job_url("https://pracuj.pl/praca/frontend-dev-acme;1234567890")


def test_pracuj_is_job_url_negative() -> None:
    c = PlaywrightPracujCollector()
    assert not c.is_job_url("https://pracuj.pl/praca")
    assert not c.is_job_url("https://nofluffjobs.com/job/react")


# ---------------------------------------------------------------------------
# extract_links_from_page (mocked Page)
# ---------------------------------------------------------------------------


def test_extract_links_from_page_resolves_relative() -> None:
    c = PlaywrightJustJoinCollector()
    mock_page = MagicMock()
    mock_page.eval_on_selector_all.return_value = [
        "https://justjoin.it/offers/react-dev",
        "/offers/another-dev",
        "//justjoin.it/offers/third",
    ]
    links = c.extract_links_from_page(mock_page, "https://justjoin.it/job-offers")
    assert "https://justjoin.it/offers/react-dev" in links
    assert "https://justjoin.it/offers/another-dev" in links
    assert "https://justjoin.it/offers/third" in links


def test_extract_links_filters_javascript_and_mailto() -> None:
    c = PlaywrightJustJoinCollector()
    mock_page = MagicMock()
    mock_page.eval_on_selector_all.return_value = [
        "javascript:void(0)",
        "mailto:jobs@company.com",
        "#top",
        "https://justjoin.it/offers/valid-job",
    ]
    links = c.extract_links_from_page(mock_page, "https://justjoin.it")
    assert len(links) == 1
    assert links[0] == "https://justjoin.it/offers/valid-job"


def test_extract_links_handles_page_error() -> None:
    c = PlaywrightJustJoinCollector()
    mock_page = MagicMock()
    mock_page.eval_on_selector_all.side_effect = RuntimeError("page crashed")
    links = c.extract_links_from_page(mock_page, "https://justjoin.it")
    assert links == []


# ---------------------------------------------------------------------------
# collect_urls — blocked page detection
# ---------------------------------------------------------------------------


def _make_mock_playwright_context(page_text: str, hrefs: list[str]):
    """Return (mock_sync_playwright_cm, mock_page) that simulate a Playwright run."""
    mock_page = MagicMock()
    mock_page.inner_text.return_value = page_text
    mock_page.eval_on_selector_all.return_value = hrefs
    mock_page.goto.return_value = None
    mock_page.wait_for_load_state.return_value = None
    mock_page.evaluate.return_value = None
    mock_page.screenshot.return_value = None
    mock_page.close.return_value = None

    mock_context = MagicMock()
    mock_context.new_page.return_value = mock_page
    mock_context.close.return_value = None

    mock_browser = MagicMock()
    mock_browser.new_context.return_value = mock_context
    mock_browser.close.return_value = None

    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    mock_pw_cm = MagicMock()
    mock_pw_cm.__enter__ = MagicMock(return_value=mock_pw)
    mock_pw_cm.__exit__ = MagicMock(return_value=False)

    return mock_pw_cm, mock_page


def test_collect_urls_captcha_page_returns_warning(
    broad_criteria: JobSearchCriteria, minimal_cfg, tmp_path: object, monkeypatch
) -> None:
    """If the page shows a CAPTCHA, collect_urls returns 0 URLs and a warning."""
    mock_pw_cm, _ = _make_mock_playwright_context(
        "Just a moment… checking your browser",
        ["https://justjoin.it/offers/react-dev"],
    )
    monkeypatch.setattr(
        "cv_sender.collectors.playwright_base._DEBUG_BASE",
        __import__("pathlib").Path(str(tmp_path)),
    )
    with patch("cv_sender.collectors.playwright_base.sync_playwright", return_value=mock_pw_cm):
        c = PlaywrightJustJoinCollector()
        result = c.collect_urls(broad_criteria, minimal_cfg)

    assert result.job_url_count == 0
    assert any("captcha" in w.lower() for w in result.warnings)


def test_collect_urls_collects_job_urls(
    broad_criteria: JobSearchCriteria, minimal_cfg, tmp_path: object, monkeypatch
) -> None:
    """Happy path: page has job links → they are collected."""
    job_hrefs = [
        "https://justjoin.it/offers/react-developer-acme",
        "https://justjoin.it/offers/frontend-engineer-beta",
        "https://justjoin.it/job-offers",  # listing page, should be rejected
        "https://www.google.com/",          # non-job URL, should be rejected
    ]
    mock_pw_cm, _ = _make_mock_playwright_context("10 React jobs in Warsaw", job_hrefs)
    monkeypatch.setattr(
        "cv_sender.collectors.playwright_base._DEBUG_BASE",
        __import__("pathlib").Path(str(tmp_path)),
    )
    with patch("cv_sender.collectors.playwright_base.sync_playwright", return_value=mock_pw_cm):
        c = PlaywrightJustJoinCollector()
        result = c.collect_urls(broad_criteria, minimal_cfg)

    assert result.job_url_count == 2
    urls = [cu.url for cu in result.collected_urls]
    assert "https://justjoin.it/offers/react-developer-acme" in urls
    assert "https://justjoin.it/offers/frontend-engineer-beta" in urls


def test_collect_urls_deduplicates(  # noqa: PLR0912
    minimal_cfg, tmp_path: object, monkeypatch
) -> None:
    """Same URL appearing on multiple scrolls should not be collected twice."""
    # Use single keyword so only 1 listing URL is generated
    single_kw_criteria = JobSearchCriteria(
        keywords=["React"],
        technologies=[],
        locations=[],
        seniority=[],
        contract_types=[],
        min_salary_b2b=0,
        require_salary=False,
        max_offers_per_source=20,
        max_total_offers=100,
        exclude_keywords=[],
    )
    job_hrefs = [
        "https://justjoin.it/offers/react-dev?utm_source=a",
        "https://justjoin.it/offers/react-dev?utm_source=b",  # same after normalization
    ]
    mock_pw_cm, _ = _make_mock_playwright_context("10 React jobs", job_hrefs)
    monkeypatch.setattr(
        "cv_sender.collectors.playwright_base._DEBUG_BASE",
        __import__("pathlib").Path(str(tmp_path)),
    )
    with patch("cv_sender.collectors.playwright_base.sync_playwright", return_value=mock_pw_cm):
        c = PlaywrightJustJoinCollector()
        result = c.collect_urls(single_kw_criteria, minimal_cfg)

    assert result.job_url_count == 1
    assert result.duplicate_count >= 1  # same URL seen on every scroll pass


def test_collect_urls_playwright_not_installed(
    broad_criteria: JobSearchCriteria, minimal_cfg
) -> None:
    """If playwright is not installed (module-level import returned None), returns an error result."""
    c = PlaywrightJustJoinCollector()
    # Simulate the case where playwright was not installed at import time
    with patch("cv_sender.collectors.playwright_base.sync_playwright", None):
        result = c.collect_urls(broad_criteria, minimal_cfg)

    assert result.job_url_count == 0
    assert any("playwright" in e.lower() or "not installed" in e.lower() for e in result.errors)


def test_collect_urls_no_listing_urls(broad_criteria: JobSearchCriteria, minimal_cfg) -> None:
    """Collector with no listing URLs returns error result."""

    class _EmptyCollector(PlaywrightJobCollector):
        source = "empty"

        def build_search_urls(self, criteria):
            return []

        def is_job_url(self, url):
            return False

    c = _EmptyCollector()
    result = c.collect_urls(broad_criteria, minimal_cfg)
    assert result.job_url_count == 0
    assert result.errors


def test_collect_urls_raw_positive_job_zero_emits_warning(
    broad_criteria: JobSearchCriteria, minimal_cfg, tmp_path: object, monkeypatch
) -> None:
    """If raw links > 0 but none match is_job_url, a diagnostic warning is produced."""
    non_job_hrefs = [
        "https://justjoin.it/companies/acme",
        "https://justjoin.it/blog/react-trends",
        "https://justjoin.it/faq",
    ]
    mock_pw_cm, _ = _make_mock_playwright_context("Welcome to JustJoin.it", non_job_hrefs)
    monkeypatch.setattr(
        "cv_sender.collectors.playwright_base._DEBUG_BASE",
        __import__("pathlib").Path(str(tmp_path)),
    )
    with patch("cv_sender.collectors.playwright_base.sync_playwright", return_value=mock_pw_cm):
        c = PlaywrightJustJoinCollector()
        result = c.collect_urls(broad_criteria, minimal_cfg)

    assert result.job_url_count == 0
    assert any(
        "0 matched" in w or "sample rejected" in w.lower() or "job url" in w.lower()
        for w in result.warnings
    )


# ---------------------------------------------------------------------------
# Service layer — collect_job_urls
# ---------------------------------------------------------------------------


def test_collect_job_urls_unknown_source(
    broad_criteria: JobSearchCriteria, minimal_cfg
) -> None:
    """Unknown source name produces an error result without crashing."""
    from cv_sender.playwright_collection import collect_job_urls

    results = collect_job_urls(broad_criteria, ["nonexistent_source"], minimal_cfg)
    assert len(results) == 1
    assert results[0].source == "nonexistent_source"
    assert results[0].errors


def test_collect_job_urls_returns_one_result_per_source(
    broad_criteria: JobSearchCriteria, minimal_cfg, tmp_path: object, monkeypatch
) -> None:
    """collect_job_urls returns exactly one PlaywrightCollectionResult per requested source."""
    mock_pw_cm, _ = _make_mock_playwright_context("Jobs", [])
    monkeypatch.setattr(
        "cv_sender.collectors.playwright_base._DEBUG_BASE",
        __import__("pathlib").Path(str(tmp_path)),
    )
    with patch("cv_sender.collectors.playwright_base.sync_playwright", return_value=mock_pw_cm):
        from cv_sender.playwright_collection import collect_job_urls

        results = collect_job_urls(broad_criteria, ["justjoin", "rocketjobs"], minimal_cfg)

    assert len(results) == 2
    assert {r.source for r in results} == {"justjoin", "rocketjobs"}


def test_collect_and_import_calls_import_function(
    broad_criteria: JobSearchCriteria, minimal_cfg, tmp_path: object, monkeypatch
) -> None:
    """collect_and_import calls import_offers_from_urls with the collected URLs."""
    job_hrefs = [
        "https://justjoin.it/offers/react-dev-acme",
    ]
    mock_pw_cm, _ = _make_mock_playwright_context("Jobs", job_hrefs)
    monkeypatch.setattr(
        "cv_sender.collectors.playwright_base._DEBUG_BASE",
        __import__("pathlib").Path(str(tmp_path)),
    )

    from cv_sender.models import BatchImportResult

    mock_import_result = BatchImportResult()

    with (
        patch("cv_sender.collectors.playwright_base.sync_playwright", return_value=mock_pw_cm),
        patch(
            "cv_sender.playwright_collection.import_offers_from_urls",
            return_value=mock_import_result,
        ) as mock_import,
    ):
        from cv_sender.playwright_collection import collect_and_import

        summary = collect_and_import(broad_criteria, ["justjoin"], minimal_cfg, auto_score=False)

    mock_import.assert_called_once()
    called_urls = mock_import.call_args.kwargs.get("urls") or mock_import.call_args.args[0]
    assert "https://justjoin.it/offers/react-dev-acme" in called_urls
    assert summary["total_collected"] == 1


def test_collect_and_import_skips_import_on_empty_collection(
    broad_criteria: JobSearchCriteria, minimal_cfg, tmp_path: object, monkeypatch
) -> None:
    """collect_and_import does NOT call import when no URLs were collected."""
    mock_pw_cm, _ = _make_mock_playwright_context("Jobs", [])
    monkeypatch.setattr(
        "cv_sender.collectors.playwright_base._DEBUG_BASE",
        __import__("pathlib").Path(str(tmp_path)),
    )

    with (
        patch("cv_sender.collectors.playwright_base.sync_playwright", return_value=mock_pw_cm),
        patch("cv_sender.playwright_collection.import_offers_from_urls") as mock_import,
    ):
        from cv_sender.playwright_collection import collect_and_import

        summary = collect_and_import(broad_criteria, ["justjoin"], minimal_cfg, auto_score=False)

    mock_import.assert_not_called()
    assert summary["total_collected"] == 0
    assert summary["total_imported"] == 0


# ---------------------------------------------------------------------------
# PlaywrightCollectionConfig in Settings
# ---------------------------------------------------------------------------


def test_playwright_collection_config_defaults() -> None:
    from cv_sender.config import PlaywrightCollectionConfig

    cfg = PlaywrightCollectionConfig()
    assert cfg.enabled is True
    assert cfg.headless is False
    assert cfg.max_urls_per_source == 50


def test_settings_has_playwright_collection() -> None:
    from cv_sender.config import Settings

    s = Settings()
    assert hasattr(s, "playwright_collection")
    from cv_sender.config import PlaywrightCollectionConfig

    assert isinstance(s.playwright_collection, PlaywrightCollectionConfig)


def test_load_settings_parses_playwright_collection(tmp_path, monkeypatch) -> None:
    """load_settings parses playwright_collection dict from YAML into the model."""
    import yaml

    from cv_sender.config import load_settings

    yaml_data = {
        "role": "",
        "technologies": [],
        "locations": [],
        "contract_types": [],
        "exclude_keywords": [],
        "job_search": {
            "enabled": False,
            "keywords": [],
            "technologies": [],
            "locations": [],
            "seniority": [],
            "contract_types": [],
            "exclude_keywords": [],
            "sources": {},
        },
        "playwright_collection": {
            "enabled": True,
            "headless": True,
            "max_urls_per_source": 99,
            "slow_mo_ms": 77,
        },
    }
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(yaml.dump(yaml_data), encoding="utf-8")

    monkeypatch.setenv("SETTINGS_PATH", str(settings_file))
    settings = load_settings()

    assert settings.playwright_collection.headless is True
    assert settings.playwright_collection.max_urls_per_source == 99
    assert settings.playwright_collection.slow_mo_ms == 77
