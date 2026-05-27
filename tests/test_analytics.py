"""Tests for the analytics module."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cv_sender.analytics import (
    AnalyticsData,
    FunnelMetrics,
    _salary_bucket,
    _week_label,
    calculate_cv_performance,
    calculate_funnel_metrics,
    calculate_response_rates,
    calculate_salary_analysis,
    calculate_source_performance,
    calculate_technology_performance,
    calculate_time_metrics,
    calculate_weekly_activity,
    export_analytics_csv,
    generate_deterministic_insights,
)
from cv_sender.models import (
    Application,
    ApplicationEvent,
    ApplicationStatus,
    Decision,
    Interview,
    InterviewStatus,
    Offer,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _days_ago(n: int) -> datetime:
    return _now() - timedelta(days=n)


def _make_offer(
    *,
    id: str = "offer-1",
    decision: Decision | None = None,
    technologies: list[str] | None = None,
    salary_min: float | None = None,
    salary_max: float | None = None,
    source: str = "justjoin",
) -> Offer:
    return Offer(
        id=id,
        url=f"https://example.com/{id}",
        title="Dev",
        company="ACME",
        decision=decision,
        technologies=technologies or [],
        salary_min=salary_min,
        salary_max=salary_max,
        source=source,
    )


def _make_app(
    *,
    id: str = "app-1",
    offer_id: str = "offer-1",
    status: ApplicationStatus = ApplicationStatus.SENT,
    source: str = "justjoin",
    sent_at: datetime | None = None,
    last_contact_at: datetime | None = None,
    interview_at: datetime | None = None,
    selected_cv_name: str = "",
    salary_min: float | None = None,
    salary_max: float | None = None,
    events: list[ApplicationEvent] | None = None,
) -> Application:
    return Application(
        id=id,
        offer_id=offer_id,
        status=status,
        source=source,
        sent_at=sent_at,
        last_contact_at=last_contact_at,
        interview_at=interview_at,
        selected_cv_name=selected_cv_name,
        salary_min=salary_min,
        salary_max=salary_max,
        events=events or [],
    )


def _data(*apps: Application, offers: list[Offer] | None = None) -> AnalyticsData:
    return AnalyticsData(applications=list(apps), offers=offers or [])


# ---------------------------------------------------------------------------
# Funnel metrics
# ---------------------------------------------------------------------------


def test_funnel_counts_sent_applications() -> None:
    a1 = _make_app(status=ApplicationStatus.SENT)
    a2 = _make_app(id="app-2", status=ApplicationStatus.REPLY_RECEIVED)
    a3 = _make_app(id="app-3", status=ApplicationStatus.NEW)
    data = _data(a1, a2, a3)

    fm = calculate_funnel_metrics(data)

    assert fm.sent == 2      # SENT and REPLY_RECEIVED both count as sent
    assert fm.reply_received == 1
    assert fm.interview == 0


def test_funnel_counts_offer_decisions() -> None:
    offers = [
        _make_offer(id="o1", decision=Decision.APPLY),
        _make_offer(id="o2", decision=Decision.APPLY),
        _make_offer(id="o3", decision=Decision.SKIP),
        _make_offer(id="o4", decision=Decision.MAYBE),
    ]
    data = AnalyticsData(applications=[], offers=offers)
    fm = calculate_funnel_metrics(data)

    assert fm.imported == 4
    assert fm.apply_scored == 2
    assert fm.skip_scored == 1
    assert fm.maybe_scored == 1


def test_funnel_all_terminal_statuses() -> None:
    apps = [
        _make_app(id="a1", status=ApplicationStatus.INTERVIEW),
        _make_app(id="a2", status=ApplicationStatus.OFFER),
        _make_app(id="a3", status=ApplicationStatus.REJECTED),
        _make_app(id="a4", status=ApplicationStatus.NO_RESPONSE),
    ]
    data = _data(*apps)
    fm = calculate_funnel_metrics(data)

    assert fm.interview == 1
    assert fm.offer == 1
    assert fm.rejected == 1
    assert fm.no_response == 1


def test_funnel_rates() -> None:
    fm = FunnelMetrics(sent=10, reply_received=3, interview=1, offer=1)
    assert fm.sent_to_reply_rate() == 30.0
    assert fm.sent_to_interview_rate() == 10.0
    assert fm.sent_to_offer_rate() == 10.0


def test_funnel_rates_zero_sent() -> None:
    fm = FunnelMetrics(sent=0)
    assert fm.sent_to_reply_rate() == 0.0
    assert fm.sent_to_interview_rate() == 0.0


# ---------------------------------------------------------------------------
# Response rates
# ---------------------------------------------------------------------------


def test_response_rate_calculation() -> None:
    apps = [
        _make_app(id="a1", status=ApplicationStatus.SENT),
        _make_app(id="a2", status=ApplicationStatus.REPLY_RECEIVED),
        _make_app(id="a3", status=ApplicationStatus.INTERVIEW),
        _make_app(id="a4", status=ApplicationStatus.REJECTED),
        _make_app(id="a5", status=ApplicationStatus.OFFER),
    ]
    data = _data(*apps)
    rr = calculate_response_rates(data)

    assert rr.sent == 5
    assert rr.replies == 4    # REPLY_RECEIVED + INTERVIEW + REJECTED + OFFER (all are replies)
    assert rr.interviews == 1
    assert rr.rejections == 1
    assert rr.offers == 1
    assert rr.response_rate() == 80.0
    assert rr.interview_rate() == 20.0
    assert rr.rejection_rate() == 20.0
    assert rr.offer_rate() == 20.0


def test_response_rate_empty() -> None:
    rr = calculate_response_rates(_data())
    assert rr.response_rate() == 0.0
    assert rr.interview_rate() == 0.0


# ---------------------------------------------------------------------------
# Time metrics
# ---------------------------------------------------------------------------


def test_avg_days_to_reply() -> None:
    sent = _days_ago(10)
    contact = _days_ago(3)   # 7 days after sent
    a = _make_app(
        status=ApplicationStatus.REPLY_RECEIVED,
        sent_at=sent,
        last_contact_at=contact,
    )
    tm = calculate_time_metrics(_data(a))
    assert tm.avg_days_to_reply is not None
    assert abs(tm.avg_days_to_reply - 7.0) < 0.1


def test_median_days_to_reply_multiple() -> None:
    apps = []
    for i, gap in enumerate([2, 4, 6]):
        apps.append(
            _make_app(
                id=f"app-{i}",
                status=ApplicationStatus.REPLY_RECEIVED,
                sent_at=_days_ago(10),
                last_contact_at=_days_ago(10 - gap),
            )
        )
    tm = calculate_time_metrics(_data(*apps))
    assert tm.median_days_to_reply == pytest.approx(4.0, abs=0.01)


def test_time_metrics_sent_last_7_30() -> None:
    a_recent = _make_app(id="a1", sent_at=_days_ago(3))
    a_old = _make_app(id="a2", sent_at=_days_ago(20))
    a_very_old = _make_app(id="a3", sent_at=_days_ago(40))
    tm = calculate_time_metrics(_data(a_recent, a_old, a_very_old))

    assert tm.sent_last_7 == 1
    assert tm.sent_last_30 == 2


def test_time_metrics_missing_sent_at() -> None:
    a = _make_app(status=ApplicationStatus.SENT, sent_at=None)
    tm = calculate_time_metrics(_data(a))
    assert tm.avg_days_to_reply is None
    assert tm.sent_last_7 == 0


# ---------------------------------------------------------------------------
# Source performance
# ---------------------------------------------------------------------------


def test_source_grouping() -> None:
    apps = [
        _make_app(id="a1", source="rocketjobs", status=ApplicationStatus.SENT),
        _make_app(id="a2", source="rocketjobs", status=ApplicationStatus.REPLY_RECEIVED),
        _make_app(id="a3", source="justjoin", status=ApplicationStatus.SENT),
    ]
    rows = calculate_source_performance(_data(*apps))

    by_source = {r["source"]: r for r in rows}
    assert by_source["rocketjobs"]["sent"] == 2
    assert by_source["rocketjobs"]["replies"] == 1
    assert by_source["rocketjobs"]["response_rate_%"] == 50.0
    assert by_source["justjoin"]["sent"] == 1
    assert by_source["justjoin"]["response_rate_%"] == 0.0


def test_source_grouping_empty_source() -> None:
    a = _make_app(source="")
    rows = calculate_source_performance(_data(a))
    sources = [r["source"] for r in rows]
    assert "other" in sources


# ---------------------------------------------------------------------------
# CV performance
# ---------------------------------------------------------------------------


def test_cv_grouping() -> None:
    apps = [
        _make_app(id="a1", selected_cv_name="React CV", status=ApplicationStatus.REPLY_RECEIVED),
        _make_app(id="a2", selected_cv_name="React CV", status=ApplicationStatus.SENT),
        _make_app(id="a3", selected_cv_name="Python CV", status=ApplicationStatus.INTERVIEW),
    ]
    rows = calculate_cv_performance(_data(*apps))
    by_cv = {r["cv_profile"]: r for r in rows}

    assert by_cv["React CV"]["sent"] == 2
    assert by_cv["React CV"]["replies"] == 1
    assert by_cv["Python CV"]["interviews"] == 1


def test_cv_grouping_no_cv() -> None:
    a = _make_app(selected_cv_name="")
    rows = calculate_cv_performance(_data(a))
    assert rows[0]["cv_profile"] == "none"


# ---------------------------------------------------------------------------
# Technology performance
# ---------------------------------------------------------------------------


def test_technology_grouping() -> None:
    offer = _make_offer(id="o1", technologies=["React", "TypeScript"])
    app_sent = _make_app(id="a1", offer_id="o1", status=ApplicationStatus.SENT)
    app_reply = _make_app(id="a2", offer_id="o1", status=ApplicationStatus.REPLY_RECEIVED)
    data = AnalyticsData(applications=[app_sent, app_reply], offers=[offer])

    rows = calculate_technology_performance(data)
    by_tech = {r["technology"]: r for r in rows}

    assert by_tech["React"]["sent"] == 2
    assert by_tech["React"]["replies"] == 1
    assert by_tech["TypeScript"]["sent"] == 2


def test_technology_grouping_no_offer() -> None:
    a = _make_app(offer_id="missing-offer")
    rows = calculate_technology_performance(_data(a))
    assert rows == []


# ---------------------------------------------------------------------------
# Salary bucket assignment
# ---------------------------------------------------------------------------


def test_salary_bucket_ranges() -> None:
    assert _salary_bucket(None) == "unknown"
    assert _salary_bucket(8_000) == "< 12k"
    assert _salary_bucket(12_000) == "12k–16k"
    assert _salary_bucket(16_000) == "16k–20k"
    assert _salary_bucket(20_000) == "20k–25k"
    assert _salary_bucket(25_000) == "25k+"
    assert _salary_bucket(30_000) == "25k+"


def test_salary_analysis_averages() -> None:
    apps = [
        _make_app(id="a1", status=ApplicationStatus.SENT, salary_min=10_000, salary_max=15_000),
        _make_app(id="a2", status=ApplicationStatus.SENT, salary_min=20_000, salary_max=25_000),
        _make_app(id="a3", status=ApplicationStatus.REPLY_RECEIVED, salary_min=20_000),
    ]
    data = _data(*apps)
    sa = calculate_salary_analysis(data)

    assert sa.avg_sent_salary_min == pytest.approx(16_666.67, rel=0.01)
    assert sa.avg_reply_salary_min == 20_000.0


def test_salary_analysis_salary_from_offer() -> None:
    offer = _make_offer(id="o1", salary_min=18_000, salary_max=22_000)
    app = _make_app(offer_id="o1", status=ApplicationStatus.SENT)
    data = AnalyticsData(applications=[app], offers=[offer])
    sa = calculate_salary_analysis(data)
    assert sa.avg_sent_salary_min == 18_000.0


def test_salary_analysis_empty() -> None:
    sa = calculate_salary_analysis(_data())
    assert sa.avg_sent_salary_min is None
    assert sa.bucket_rows == []


# ---------------------------------------------------------------------------
# Weekly activity
# ---------------------------------------------------------------------------


def test_weekly_activity_grouping() -> None:
    # Two apps in same week, one in a different week
    dt_this_week = _days_ago(2)
    dt_last_week = _days_ago(9)
    apps = [
        _make_app(id="a1", sent_at=dt_this_week, status=ApplicationStatus.SENT),
        _make_app(id="a2", sent_at=dt_this_week, status=ApplicationStatus.REPLY_RECEIVED),
        _make_app(id="a3", sent_at=dt_last_week, status=ApplicationStatus.SENT),
    ]
    rows = calculate_weekly_activity(_data(*apps))

    assert len(rows) >= 1
    totals_sent = sum(r["sent"] for r in rows)
    assert totals_sent == 3


def test_weekly_activity_label_format() -> None:
    label = _week_label(datetime(2025, 6, 2, tzinfo=UTC))
    assert label.startswith("2025-W")


def test_weekly_activity_empty() -> None:
    rows = calculate_weekly_activity(_data())
    assert rows == []


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------


def test_insights_best_source() -> None:
    fm = FunnelMetrics(sent=10, reply_received=3, interview=1, offer=0)
    rates = calculate_response_rates(_data())
    rates.sent = 10
    rates.replies = 5

    source_rows = [
        {"source": "rocketjobs", "sent": 5, "replies": 3, "interviews": 0,
         "rejected": 0, "offers": 0, "response_rate_%": 60.0, "interview_rate_%": 0.0},
        {"source": "justjoin", "sent": 5, "replies": 1, "interviews": 0,
         "rejected": 0, "offers": 0, "response_rate_%": 20.0, "interview_rate_%": 0.0},
    ]
    from cv_sender.analytics import SalaryAnalysis, TimeMetrics  # noqa: PLC0415

    insights = generate_deterministic_insights(
        fm, rates, TimeMetrics(), source_rows, [], SalaryAnalysis(), []
    )
    combined = " ".join(insights)
    assert "rocketjobs" in combined


def test_insights_not_enough_data() -> None:
    from cv_sender.analytics import FunnelMetrics, ResponseRates, SalaryAnalysis, TimeMetrics  # noqa: PLC0415

    insights = generate_deterministic_insights(
        FunnelMetrics(), ResponseRates(), TimeMetrics(), [], [], SalaryAnalysis(), []
    )
    assert insights
    assert "Not enough data" in insights[0]


def test_insights_weekly_trend() -> None:
    from cv_sender.analytics import FunnelMetrics, ResponseRates, SalaryAnalysis, TimeMetrics  # noqa: PLC0415

    weekly = [{"week": "2025-W10", "sent": 5, "replies": 0, "interviews": 0},
              {"week": "2025-W11", "sent": 2, "replies": 0, "interviews": 0}]
    insights = generate_deterministic_insights(
        FunnelMetrics(), ResponseRates(), TimeMetrics(), [], [], SalaryAnalysis(), weekly
    )
    combined = " ".join(insights)
    assert "fewer" in combined.lower() or "2" in combined


# ---------------------------------------------------------------------------
# Export CSV
# ---------------------------------------------------------------------------


def test_export_csv_contains_headers() -> None:
    source_rows = [{"source": "justjoin", "sent": 3, "replies": 1, "response_rate_%": 33.3}]
    csv_str = export_analytics_csv(source_rows, [], [], [])
    assert "Source Performance" in csv_str
    assert "justjoin" in csv_str


def test_export_csv_empty_tables() -> None:
    csv_str = export_analytics_csv([], [], [], [])
    assert "Source Performance" in csv_str
    assert "Weekly Activity" in csv_str


# ---------------------------------------------------------------------------
# Missing field handling
# ---------------------------------------------------------------------------


def test_missing_sent_at_handled_gracefully() -> None:
    apps = [_make_app(sent_at=None, status=ApplicationStatus.SENT)]
    tm = calculate_time_metrics(_data(*apps))
    assert tm.avg_days_to_reply is None
    assert tm.sent_last_7 == 0


def test_missing_selected_cv_handled() -> None:
    a = _make_app(selected_cv_name="", status=ApplicationStatus.SENT)
    rows = calculate_cv_performance(_data(a))
    assert rows[0]["cv_profile"] == "none"


def test_missing_salary_handled() -> None:
    a = _make_app(salary_min=None, salary_max=None, status=ApplicationStatus.SENT)
    sa = calculate_salary_analysis(_data(a))
    assert sa.avg_sent_salary_min is None
    assert any(r["salary_bucket"] == "unknown" for r in sa.bucket_rows)


def test_application_with_no_events() -> None:
    """Applications without events must not crash any calculation."""
    a = _make_app(events=[], status=ApplicationStatus.REPLY_RECEIVED, sent_at=_days_ago(5))
    tm = calculate_time_metrics(_data(a))
    # No last_contact_at either — should be handled gracefully
    assert tm.avg_days_to_reply is None
