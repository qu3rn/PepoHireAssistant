"""Analytics module for job-search performance tracking.

All calculations are local — no data leaves the machine.
Uses only data from offers, applications, interviews, and email matches.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from statistics import mean, median
from typing import Any

from cv_sender.models import Application, ApplicationStatus, Decision, Interview, Offer


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------


@dataclass
class AnalyticsData:
    """Raw data snapshot used by all analytics functions."""

    applications: list[Application] = field(default_factory=list)
    offers: list[Offer] = field(default_factory=list)
    interviews: list[Interview] = field(default_factory=list)

    # offer_id -> Offer lookup (built lazily)
    _offer_map: dict[str, Offer] = field(default_factory=dict, repr=False)

    def offer_for(self, app: Application) -> Offer | None:
        if not self._offer_map:
            self._offer_map = {o.id: o for o in self.offers}
        return self._offer_map.get(app.offer_id)


def load_analytics_data() -> AnalyticsData:
    """Load all local data into an :class:`AnalyticsData` snapshot."""
    from cv_sender.storage import load_applications, load_interviews, load_offers  # noqa: PLC0415

    return AnalyticsData(
        applications=load_applications(),
        offers=load_offers(),
        interviews=load_interviews(),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SENT_STATUSES: frozenset[ApplicationStatus] = frozenset(
    {
        ApplicationStatus.SENT,
        ApplicationStatus.FOLLOW_UP_DUE,
        ApplicationStatus.FOLLOW_UP_SENT,
        ApplicationStatus.REPLY_RECEIVED,
        ApplicationStatus.INTERVIEW,
        ApplicationStatus.REJECTED,
        ApplicationStatus.OFFER,
        ApplicationStatus.NO_RESPONSE,
        ApplicationStatus.ARCHIVED,
    }
)

_REPLY_STATUSES: frozenset[ApplicationStatus] = frozenset(
    {
        ApplicationStatus.REPLY_RECEIVED,
        ApplicationStatus.INTERVIEW,
        ApplicationStatus.REJECTED,
        ApplicationStatus.OFFER,
    }
)


def _is_sent(app: Application) -> bool:
    return app.status in _SENT_STATUSES


def _is_reply(app: Application) -> bool:
    return app.status in _REPLY_STATUSES


def _is_interview(app: Application) -> bool:
    return app.status == ApplicationStatus.INTERVIEW


def _is_rejected(app: Application) -> bool:
    return app.status == ApplicationStatus.REJECTED


def _is_offer(app: Application) -> bool:
    return app.status == ApplicationStatus.OFFER


def _first_event_date(app: Application, event_name: str) -> datetime | None:
    """Return timestamp of the first event matching *event_name*, or ``None``."""
    for ev in app.events:
        if ev.event == event_name:
            return ev.timestamp
    return None


def _days_between(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    # Normalise to UTC-aware
    if a.tzinfo is None:
        a = a.replace(tzinfo=UTC)
    if b.tzinfo is None:
        b = b.replace(tzinfo=UTC)
    return (b - a).total_seconds() / 86400


def _week_label(dt: datetime) -> str:
    """Return ISO week label like '2025-W22'."""
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _salary_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 12_000:
        return "< 12k"
    if value < 16_000:
        return "12k–16k"
    if value < 20_000:
        return "16k–20k"
    if value < 25_000:
        return "20k–25k"
    return "25k+"


def _filter_apps(
    data: AnalyticsData,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sources: list[str] | None = None,
    cv_names: list[str] | None = None,
    statuses: list[str] | None = None,
    technologies: list[str] | None = None,
    salary_min_floor: float | None = None,
    salary_max_ceil: float | None = None,
) -> list[Application]:
    """Return applications matching all supplied filters (all optional)."""
    apps = data.applications
    if date_from:
        if date_from.tzinfo is None:
            date_from = date_from.replace(tzinfo=UTC)
        apps = [a for a in apps if a.sent_at and _tz(a.sent_at) >= date_from]
    if date_to:
        if date_to.tzinfo is None:
            date_to = date_to.replace(tzinfo=UTC)
        apps = [a for a in apps if a.sent_at and _tz(a.sent_at) <= date_to]
    if sources:
        apps = [a for a in apps if a.source in sources]
    if cv_names:
        apps = [a for a in apps if a.selected_cv_name in cv_names]
    if statuses:
        apps = [a for a in apps if a.status in statuses]
    if technologies:
        tech_set = {t.lower() for t in technologies}
        filtered = []
        for a in apps:
            offer = data.offer_for(a)
            if offer:
                offer_techs = {t.lower() for t in offer.technologies}
                if offer_techs & tech_set:
                    filtered.append(a)
        apps = filtered
    if salary_min_floor is not None:
        apps = [
            a for a in apps
            if (a.salary_min is not None and a.salary_min >= salary_min_floor)
            or (data.offer_for(a) and data.offer_for(a).salary_min is not None
                and data.offer_for(a).salary_min >= salary_min_floor)
        ]
    if salary_max_ceil is not None:
        apps = [
            a for a in apps
            if (a.salary_max is not None and a.salary_max <= salary_max_ceil)
            or (data.offer_for(a) and data.offer_for(a).salary_max is not None
                and data.offer_for(a).salary_max <= salary_max_ceil)
        ]
    return apps


def _tz(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# 1. Funnel metrics
# ---------------------------------------------------------------------------


@dataclass
class FunnelMetrics:
    imported: int = 0
    apply_scored: int = 0
    maybe_scored: int = 0
    skip_scored: int = 0
    ready_to_send: int = 0
    sent: int = 0
    reply_received: int = 0
    interview: int = 0
    offer: int = 0
    rejected: int = 0
    no_response: int = 0

    def sent_to_reply_rate(self) -> float:
        return (self.reply_received / self.sent * 100) if self.sent else 0.0

    def sent_to_interview_rate(self) -> float:
        return (self.interview / self.sent * 100) if self.sent else 0.0

    def sent_to_offer_rate(self) -> float:
        return (self.offer / self.sent * 100) if self.sent else 0.0


def calculate_funnel_metrics(
    data: AnalyticsData, apps: list[Application] | None = None
) -> FunnelMetrics:
    apps = apps if apps is not None else data.applications

    fm = FunnelMetrics(imported=len(data.offers))

    for o in data.offers:
        if o.decision == Decision.APPLY:
            fm.apply_scored += 1
        elif o.decision == Decision.MAYBE:
            fm.maybe_scored += 1
        elif o.decision == Decision.SKIP:
            fm.skip_scored += 1

    for a in apps:
        if a.status == ApplicationStatus.READY_TO_SEND:
            fm.ready_to_send += 1
        if _is_sent(a):
            fm.sent += 1
        if _is_reply(a):
            fm.reply_received += 1
        if _is_interview(a):
            fm.interview += 1
        if _is_offer(a):
            fm.offer += 1
        if _is_rejected(a):
            fm.rejected += 1
        if a.status == ApplicationStatus.NO_RESPONSE:
            fm.no_response += 1

    return fm


# ---------------------------------------------------------------------------
# 2. Response rates
# ---------------------------------------------------------------------------


@dataclass
class ResponseRates:
    sent: int = 0
    replies: int = 0
    interviews: int = 0
    rejections: int = 0
    offers: int = 0

    def response_rate(self) -> float:
        return (self.replies / self.sent * 100) if self.sent else 0.0

    def interview_rate(self) -> float:
        return (self.interviews / self.sent * 100) if self.sent else 0.0

    def rejection_rate(self) -> float:
        return (self.rejections / self.sent * 100) if self.sent else 0.0

    def offer_rate(self) -> float:
        return (self.offers / self.sent * 100) if self.sent else 0.0


def calculate_response_rates(
    data: AnalyticsData, apps: list[Application] | None = None
) -> ResponseRates:
    apps = apps if apps is not None else data.applications
    rr = ResponseRates()
    for a in apps:
        if _is_sent(a):
            rr.sent += 1
        if _is_reply(a):
            rr.replies += 1
        if _is_interview(a):
            rr.interviews += 1
        if _is_rejected(a):
            rr.rejections += 1
        if _is_offer(a):
            rr.offers += 1
    return rr


# ---------------------------------------------------------------------------
# 3. Time metrics
# ---------------------------------------------------------------------------


@dataclass
class TimeMetrics:
    avg_days_to_reply: float | None = None
    median_days_to_reply: float | None = None
    avg_days_to_interview: float | None = None
    sent_last_7: int = 0
    sent_last_30: int = 0
    replies_last_7: int = 0
    replies_last_30: int = 0


def calculate_time_metrics(
    data: AnalyticsData, apps: list[Application] | None = None, now: datetime | None = None
) -> TimeMetrics:
    apps = apps if apps is not None else data.applications
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    cutoff_7 = now - timedelta(days=7)
    cutoff_30 = now - timedelta(days=30)

    days_to_reply: list[float] = []
    days_to_interview: list[float] = []
    tm = TimeMetrics()

    for a in apps:
        sent = a.sent_at
        if sent:
            sent_tz = _tz(sent)
            if sent_tz >= cutoff_7:
                tm.sent_last_7 += 1
            if sent_tz >= cutoff_30:
                tm.sent_last_30 += 1

        if _is_reply(a) and sent:
            # Try to find first reply event; fall back to last_contact_at
            reply_dt = _first_event_date(a, "reply_received") or a.last_contact_at
            d = _days_between(sent, reply_dt)
            if d is not None and d >= 0:
                days_to_reply.append(d)
                if _is_reply(a) and a.sent_at:
                    sent_tz = _tz(a.sent_at)
                    if sent_tz >= cutoff_7:
                        tm.replies_last_7 += 1
                    if sent_tz >= cutoff_30:
                        tm.replies_last_30 += 1

        if _is_interview(a) and sent:
            interview_dt = _first_event_date(a, "interview_scheduled") or a.interview_at
            d = _days_between(sent, interview_dt)
            if d is not None and d >= 0:
                days_to_interview.append(d)

    # Re-count replies_last_N independently (simpler)
    tm.replies_last_7 = sum(
        1 for a in apps
        if _is_reply(a) and a.sent_at and _tz(a.sent_at) >= cutoff_7
    )
    tm.replies_last_30 = sum(
        1 for a in apps
        if _is_reply(a) and a.sent_at and _tz(a.sent_at) >= cutoff_30
    )

    if days_to_reply:
        tm.avg_days_to_reply = mean(days_to_reply)
        tm.median_days_to_reply = median(days_to_reply)
    if days_to_interview:
        tm.avg_days_to_interview = mean(days_to_interview)

    return tm


# ---------------------------------------------------------------------------
# 4. Source performance
# ---------------------------------------------------------------------------


def calculate_source_performance(
    data: AnalyticsData, apps: list[Application] | None = None
) -> list[dict[str, Any]]:
    apps = apps if apps is not None else data.applications
    buckets: dict[str, dict[str, int]] = {}

    for a in apps:
        src = a.source or "other"
        if src not in buckets:
            buckets[src] = {"sent": 0, "replies": 0, "interviews": 0, "rejected": 0, "offers": 0}
        b = buckets[src]
        if _is_sent(a):
            b["sent"] += 1
        if _is_reply(a):
            b["replies"] += 1
        if _is_interview(a):
            b["interviews"] += 1
        if _is_rejected(a):
            b["rejected"] += 1
        if _is_offer(a):
            b["offers"] += 1

    rows = []
    for src, b in sorted(buckets.items()):
        sent = b["sent"]
        rows.append(
            {
                "source": src,
                "sent": sent,
                "replies": b["replies"],
                "interviews": b["interviews"],
                "rejected": b["rejected"],
                "offers": b["offers"],
                "response_rate_%": round(b["replies"] / sent * 100, 1) if sent else 0.0,
                "interview_rate_%": round(b["interviews"] / sent * 100, 1) if sent else 0.0,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 5. CV profile performance
# ---------------------------------------------------------------------------


def calculate_cv_performance(
    data: AnalyticsData, apps: list[Application] | None = None
) -> list[dict[str, Any]]:
    apps = apps if apps is not None else data.applications
    buckets: dict[str, dict[str, Any]] = {}

    for a in apps:
        cv = a.selected_cv_name or a.selected_cv_id or "none"
        if cv not in buckets:
            buckets[cv] = {"sent": 0, "replies": 0, "interviews": 0, "rejected": 0, "offers": 0}
        b = buckets[cv]
        if _is_sent(a):
            b["sent"] += 1
        if _is_reply(a):
            b["replies"] += 1
        if _is_interview(a):
            b["interviews"] += 1
        if _is_rejected(a):
            b["rejected"] += 1
        if _is_offer(a):
            b["offers"] += 1

    rows = []
    for cv, b in sorted(buckets.items()):
        sent = b["sent"]
        rows.append(
            {
                "cv_profile": cv,
                "sent": sent,
                "replies": b["replies"],
                "interviews": b["interviews"],
                "rejected": b["rejected"],
                "offers": b["offers"],
                "response_rate_%": round(b["replies"] / sent * 100, 1) if sent else 0.0,
                "interview_rate_%": round(b["interviews"] / sent * 100, 1) if sent else 0.0,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 6. Technology performance
# ---------------------------------------------------------------------------


def calculate_technology_performance(
    data: AnalyticsData, apps: list[Application] | None = None
) -> list[dict[str, Any]]:
    apps = apps if apps is not None else data.applications
    offer_map = {o.id: o for o in data.offers}

    buckets: dict[str, dict[str, int]] = {}

    for a in apps:
        offer = offer_map.get(a.offer_id)
        techs = (offer.technologies if offer else []) or []
        for tech in techs:
            t = tech.strip()
            if not t:
                continue
            if t not in buckets:
                buckets[t] = {"matching_offers": 0, "sent": 0, "replies": 0, "interviews": 0}
            b = buckets[t]
            if _is_sent(a):
                b["sent"] += 1
            if _is_reply(a):
                b["replies"] += 1
            if _is_interview(a):
                b["interviews"] += 1

    # Count matching offers per tech (independent of filtered apps)
    for o in data.offers:
        for tech in o.technologies:
            t = tech.strip()
            if t in buckets:
                buckets[t]["matching_offers"] += 1

    rows = []
    for tech, b in sorted(buckets.items()):
        sent = b["sent"]
        rows.append(
            {
                "technology": tech,
                "matching_offers": b["matching_offers"],
                "sent": sent,
                "replies": b["replies"],
                "interviews": b["interviews"],
                "response_rate_%": round(b["replies"] / sent * 100, 1) if sent else 0.0,
            }
        )
    return sorted(rows, key=lambda r: r["sent"], reverse=True)


# ---------------------------------------------------------------------------
# 7. Salary analysis
# ---------------------------------------------------------------------------


@dataclass
class SalaryAnalysis:
    avg_sent_salary_min: float | None = None
    avg_sent_salary_max: float | None = None
    avg_reply_salary_min: float | None = None
    avg_interview_salary_min: float | None = None
    bucket_rows: list[dict[str, Any]] = field(default_factory=list)


def _salary_for_app(app: Application, offers: dict[str, Offer]) -> tuple[float | None, float | None]:
    sal_min = app.salary_min
    sal_max = app.salary_max
    if sal_min is None or sal_max is None:
        offer = offers.get(app.offer_id)
        if offer:
            sal_min = sal_min if sal_min is not None else offer.salary_min
            sal_max = sal_max if sal_max is not None else offer.salary_max
    return sal_min, sal_max


def calculate_salary_analysis(
    data: AnalyticsData, apps: list[Application] | None = None
) -> SalaryAnalysis:
    apps = apps if apps is not None else data.applications
    offer_map = {o.id: o for o in data.offers}

    sent_mins: list[float] = []
    sent_maxs: list[float] = []
    reply_mins: list[float] = []
    interview_mins: list[float] = []

    bucket_data: dict[str, dict[str, int]] = {}

    for a in apps:
        sal_min, sal_max = _salary_for_app(a, offer_map)
        bucket = _salary_bucket(sal_min)
        if bucket not in bucket_data:
            bucket_data[bucket] = {"sent": 0, "replies": 0, "interviews": 0}
        b = bucket_data[bucket]

        if _is_sent(a):
            b["sent"] += 1
            if sal_min is not None:
                sent_mins.append(sal_min)
            if sal_max is not None:
                sent_maxs.append(sal_max)
        if _is_reply(a):
            b["replies"] += 1
            if sal_min is not None:
                reply_mins.append(sal_min)
        if _is_interview(a):
            if sal_min is not None:
                interview_mins.append(sal_min)

    bucket_order = ["< 12k", "12k–16k", "16k–20k", "20k–25k", "25k+", "unknown"]
    bucket_rows = []
    for bk in bucket_order:
        if bk in bucket_data:
            b = bucket_data[bk]
            sent = b["sent"]
            bucket_rows.append(
                {
                    "salary_bucket": bk,
                    "sent": sent,
                    "replies": b["replies"],
                    "interviews": b["interviews"],
                    "response_rate_%": round(b["replies"] / sent * 100, 1) if sent else 0.0,
                }
            )

    return SalaryAnalysis(
        avg_sent_salary_min=mean(sent_mins) if sent_mins else None,
        avg_sent_salary_max=mean(sent_maxs) if sent_maxs else None,
        avg_reply_salary_min=mean(reply_mins) if reply_mins else None,
        avg_interview_salary_min=mean(interview_mins) if interview_mins else None,
        bucket_rows=bucket_rows,
    )


# ---------------------------------------------------------------------------
# 8. Weekly activity
# ---------------------------------------------------------------------------


def calculate_weekly_activity(
    data: AnalyticsData, apps: list[Application] | None = None
) -> list[dict[str, Any]]:
    """Return per-ISO-week counts of sent, replies, and interviews."""
    apps = apps if apps is not None else data.applications

    weeks: dict[str, dict[str, int]] = {}

    def _touch(week: str) -> dict[str, int]:
        if week not in weeks:
            weeks[week] = {"sent": 0, "replies": 0, "interviews": 0}
        return weeks[week]

    for a in apps:
        if a.sent_at:
            w = _week_label(_tz(a.sent_at))
            _touch(w)["sent"] += 1
            if _is_reply(a):
                _touch(w)["replies"] += 1
        if a.interview_at:
            w = _week_label(_tz(a.interview_at))
            _touch(w)["interviews"] += 1

    return [
        {"week": w, **counts}
        for w, counts in sorted(weeks.items())
    ]


# ---------------------------------------------------------------------------
# 9. Deterministic insights
# ---------------------------------------------------------------------------


def generate_deterministic_insights(
    funnel: FunnelMetrics,
    rates: ResponseRates,
    time_metrics: TimeMetrics,
    source_rows: list[dict],
    cv_rows: list[dict],
    salary: SalaryAnalysis,
    weekly: list[dict],
) -> list[str]:
    """Return a list of plain-English insight strings derived from local data."""
    insights: list[str] = []

    # Source with highest response rate
    src_with_data = [r for r in source_rows if r["sent"] >= 3]
    if src_with_data:
        best_src = max(src_with_data, key=lambda r: r["response_rate_%"])
        if best_src["response_rate_%"] > 0:
            insights.append(
                f"'{best_src['source']}' has the highest response rate "
                f"({best_src['response_rate_%']:.0f}% of {best_src['sent']} sent)."
            )

    # CV with most interviews
    cv_with_interviews = [r for r in cv_rows if r["interviews"] > 0]
    if cv_with_interviews:
        best_cv = max(cv_with_interviews, key=lambda r: r["interviews"])
        insights.append(
            f"CV profile '{best_cv['cv_profile']}' generated the most interviews "
            f"({best_cv['interviews']})."
        )

    # Overall response rate
    if rates.sent >= 5:
        rr = rates.response_rate()
        if rr == 0:
            insights.append("No replies yet — consider revising your CV or cover letter.")
        elif rr < 10:
            insights.append(
                f"Response rate is low ({rr:.0f}%). "
                "Consider targeting offers more closely matching your profile."
            )
        elif rr >= 30:
            insights.append(f"Strong response rate: {rr:.0f}% of sent applications got a reply.")

    # Salary insight
    if salary.bucket_rows:
        best_bucket = max(salary.bucket_rows, key=lambda r: r["response_rate_%"])
        if best_bucket["response_rate_%"] > 0 and best_bucket["sent"] >= 2:
            insights.append(
                f"The '{best_bucket['salary_bucket']}' salary range has the highest "
                f"response rate ({best_bucket['response_rate_%']:.0f}%)."
            )

    # Weekly trend
    if len(weekly) >= 2:
        last = weekly[-1]["sent"]
        prev = weekly[-2]["sent"]
        if last < prev:
            insights.append(
                f"You sent fewer applications this week ({last}) than last week ({prev})."
            )
        elif last > prev:
            insights.append(
                f"Activity is up — {last} applications sent this week vs {prev} last week."
            )

    # Interview conversion
    if rates.interviews > 0 and funnel.interview > 0:
        if funnel.offer == 0:
            insights.append(
                f"You've had {funnel.interview} interview(s) but no offers yet — keep going!"
            )

    # Time to reply
    if time_metrics.avg_days_to_reply is not None:
        insights.append(
            f"Average time to first reply: {time_metrics.avg_days_to_reply:.1f} days "
            f"(median {time_metrics.median_days_to_reply:.1f} days)."
        )

    if not insights:
        insights.append("Not enough data yet. Send more applications to see insights.")

    return insights


# ---------------------------------------------------------------------------
# 10. Export
# ---------------------------------------------------------------------------


def export_analytics_csv(
    source_rows: list[dict],
    cv_rows: list[dict],
    weekly: list[dict],
    tech_rows: list[dict],
) -> str:
    """Return a multi-section CSV string suitable for download."""
    buf = io.StringIO()
    writer = csv.writer(buf)

    sections = [
        ("Source Performance", source_rows),
        ("CV Profile Performance", cv_rows),
        ("Weekly Activity", weekly),
        ("Technology Performance", tech_rows),
    ]

    for title, rows in sections:
        writer.writerow([title])
        if rows:
            writer.writerow(list(rows[0].keys()))
            for row in rows:
                writer.writerow(list(row.values()))
        writer.writerow([])  # blank separator

    return buf.getvalue()


# ---------------------------------------------------------------------------
# LLM summary helper
# ---------------------------------------------------------------------------


def build_llm_analytics_prompt(
    funnel: FunnelMetrics,
    rates: ResponseRates,
    time_metrics: TimeMetrics,
    source_rows: list[dict],
    cv_rows: list[dict],
) -> str:
    """Build a prompt for LLM analytics summary using aggregated data only.

    No personal email content, company names, or application URLs are included.
    """
    lines = [
        "You are a job-search coach. Analyse the following aggregated job-search statistics "
        "and provide a short (3-5 sentence) actionable summary with suggestions.\n",
        f"Funnel: {funnel.imported} offers imported, {funnel.sent} applications sent, "
        f"{funnel.reply_received} replies, {funnel.interview} interviews, {funnel.offer} offers.\n",
        f"Response rate: {rates.response_rate():.1f}%  |  "
        f"Interview rate: {rates.interview_rate():.1f}%  |  "
        f"Offer rate: {rates.offer_rate():.1f}%\n",
    ]
    if time_metrics.avg_days_to_reply is not None:
        lines.append(
            f"Avg days to first reply: {time_metrics.avg_days_to_reply:.1f}  |  "
            f"Median: {time_metrics.median_days_to_reply:.1f}\n"
        )
    if source_rows:
        lines.append("Source performance (sent / response_rate_%):")
        for r in source_rows:
            lines.append(f"  {r['source']}: {r['sent']} sent, {r['response_rate_%']}% response rate")
        lines.append("")
    if cv_rows:
        lines.append("CV profile performance (sent / response_rate_%):")
        for r in cv_rows:
            lines.append(f"  {r['cv_profile']}: {r['sent']} sent, {r['response_rate_%']}% response rate")
        lines.append("")
    lines.append("Please provide a concise, actionable summary and 2-3 improvement suggestions.")
    return "\n".join(lines)
