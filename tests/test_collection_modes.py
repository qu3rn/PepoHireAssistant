from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from cv_sender.collector_diagnostics import CollectionDiagnostics, SourceSummary
from cv_sender.collectors.base import JobCollectionResult, JobSearchCriteria
from cv_sender.collectors.playwright_base import PlaywrightCollectionResult
from cv_sender.config import JobSearchConfig, PlaywrightCollectionConfig, Settings
from cv_sender.job_search import collect_jobs
from cv_sender.models import Campaign


runner = CliRunner()


def _criteria() -> JobSearchCriteria:
    return JobSearchCriteria(
        keywords=["React"],
        technologies=["React", "TypeScript", "Next.js"],
        locations=["Remote"],
        seniority=[],
        contract_types=[],
        min_salary_b2b=0,
        require_salary=False,
        max_offers_per_source=10,
        max_total_offers=20,
        exclude_keywords=[],
        request_delay_seconds=0.0,
    )


def _settings_with_mode(mode: str) -> Settings:
    cfg = JobSearchConfig(collector_mode=mode)
    cfg.sources["justjoin"].enabled = True
    cfg.sources["rocketjobs"].enabled = True
    return Settings(job_search=cfg, playwright_collection=PlaywrightCollectionConfig())


def test_collect_jobs_default_mode_is_playwright_when_missing() -> None:
    settings = _settings_with_mode("")

    with (
        patch("cv_sender.config.load_settings", return_value=settings),
        patch(
            "cv_sender.playwright_collection.collect_import_and_score_with_playwright",
            return_value=CollectionDiagnostics(source_summaries=[]),
        ) as mock_playwright,
    ):
        collect_jobs(_criteria(), mode=None, source_names=["justjoin"], auto_score=False)

    mock_playwright.assert_called_once()


def test_collect_jobs_mode_playwright_calls_playwright_pipeline() -> None:
    settings = _settings_with_mode("playwright")

    with (
        patch("cv_sender.config.load_settings", return_value=settings),
        patch(
            "cv_sender.playwright_collection.collect_import_and_score_with_playwright",
            return_value=CollectionDiagnostics(source_summaries=[]),
        ) as mock_playwright,
    ):
        collect_jobs(_criteria(), mode="playwright", source_names=["justjoin"], auto_score=False)

    mock_playwright.assert_called_once()


def test_collect_jobs_mode_api_calls_api_static_pipeline() -> None:
    settings = _settings_with_mode("playwright")
    api_result = JobCollectionResult(
        source="justjoin",
        raw_found_count=3,
        collected_count=2,
        imported_count=1,
        duplicate_count=0,
        skipped_count=1,
        failed_count=0,
    )

    with (
        patch("cv_sender.config.load_settings", return_value=settings),
        patch("cv_sender.job_search.run_job_collection", return_value=[api_result]) as mock_api,
        patch("cv_sender.job_search.collect_job_urls") as mock_pw_collect,
    ):
        report = collect_jobs(_criteria(), mode="api", source_names=["justjoin"], auto_score=False)

    mock_api.assert_called_once()
    mock_pw_collect.assert_not_called()
    assert report.source_summaries[0].collector_used == "api/static"


def test_collect_jobs_mode_hybrid_calls_api_first() -> None:
    settings = _settings_with_mode("hybrid")
    api_result = JobCollectionResult(
        source="justjoin",
        raw_found_count=5,
        collected_count=3,
        imported_count=2,
        duplicate_count=0,
        skipped_count=1,
        failed_count=0,
    )

    with (
        patch("cv_sender.config.load_settings", return_value=settings),
        patch("cv_sender.job_search.run_job_collection", return_value=[api_result]) as mock_api,
        patch("cv_sender.job_search.collect_job_urls") as mock_pw_collect,
    ):
        report = collect_jobs(_criteria(), mode="hybrid", source_names=["justjoin"], auto_score=False)

    mock_api.assert_called_once()
    mock_pw_collect.assert_not_called()
    assert report.source_summaries[0].collector_used == "hybrid_api"


def test_hybrid_fallback_calls_playwright_if_api_raw_found_zero() -> None:
    settings = _settings_with_mode("hybrid")
    settings.job_search.fallback_to_playwright = True
    api_result = JobCollectionResult(
        source="justjoin",
        raw_found_count=0,
        collected_count=0,
        imported_count=0,
        duplicate_count=0,
        skipped_count=0,
        failed_count=0,
    )
    pw_result = PlaywrightCollectionResult(source="justjoin", raw_link_count=4)
    import_summary = JobCollectionResult(source="justjoin", imported_count=1)

    with (
        patch("cv_sender.config.load_settings", return_value=settings),
        patch("cv_sender.job_search.run_job_collection", return_value=[api_result]),
        patch("cv_sender.job_search.collect_job_urls", return_value=[pw_result]) as mock_pw_collect,
        patch("cv_sender.job_search.import_collected_urls", return_value=import_summary),
    ):
        report = collect_jobs(_criteria(), mode="hybrid", source_names=["justjoin"], auto_score=False)

    mock_pw_collect.assert_called_once()
    assert report.source_summaries[0].collector_used == "hybrid_playwright_fallback"
    assert any("fallback was used" in w.lower() for w in report.global_warnings)


def test_emergency_react_cli_defaults_to_playwright() -> None:
    from cv_sender.cli import app  # noqa: PLC0415

    settings = _settings_with_mode("api_static")

    with (
        patch("cv_sender.config.load_settings", return_value=settings),
        patch("cv_sender.job_search.collect_jobs") as mock_collect,
    ):
        result = runner.invoke(app, ["collect-jobs", "--emergency", "--no-score"])

    assert result.exit_code == 0
    assert mock_collect.call_args.kwargs.get("mode") == "playwright"


def test_cli_mode_overrides_config() -> None:
    from cv_sender.cli import app  # noqa: PLC0415

    settings = _settings_with_mode("playwright")

    with (
        patch("cv_sender.config.load_settings", return_value=settings),
        patch("cv_sender.job_search.collect_jobs") as mock_collect,
    ):
        result = runner.invoke(app, ["collect-jobs", "--mode", "api", "--no-score"])

    assert result.exit_code == 0
    assert mock_collect.call_args.kwargs.get("mode") == "api"


def test_campaign_uses_global_collector_mode_if_no_override() -> None:
    from cv_sender.campaigns import resolve_campaign_collector_mode  # noqa: PLC0415

    campaign = Campaign(name="Sprint", collector_mode="")
    resolved = resolve_campaign_collector_mode(campaign, "playwright")
    assert resolved == "playwright"
