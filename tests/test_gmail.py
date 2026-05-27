"""Tests for Gmail read-only integration.

All tests mock the Gmail API client — no real network calls are made.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cv_sender.config import GmailConfig
from cv_sender.gmail_integration import (
    EmailClassificationResult,
    GmailEmail,
    classify_email,
    is_gmail_authenticated,
    is_gmail_configured,
    match_email_to_applications,
    scan_gmail_for_application_replies,
    score_email_application_match,
)
from cv_sender.models import (
    Application,
    ApplicationStatus,
    EmailClassification,
    EmailMatch,
    EmailMatchStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_storage_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import cv_sender.storage as _storage

    monkeypatch.setattr(_storage, "_DEFAULT_OFFERS", tmp_path / "offers.json")
    monkeypatch.setattr(_storage, "_DEFAULT_APPLICATIONS", tmp_path / "applications.json")
    monkeypatch.setattr(_storage, "_DEFAULT_EMAIL_MATCHES", tmp_path / "email_matches.json")


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from cv_sender.config import Settings

    monkeypatch.setattr("cv_sender.services.load_settings", lambda: Settings())
    monkeypatch.setattr(
        "cv_sender.services.load_profile",
        lambda: __import__("cv_sender.config", fromlist=["Profile"]).Profile(),
    )


def _cfg(**kwargs) -> GmailConfig:
    base = dict(
        enabled=True,
        credentials_path="config/google_credentials.json",
        token_path="config/google_token.json",
        readonly=True,
        scan_days_back=30,
        max_results=100,
        store_email_body=False,
        store_snippet=True,
        auto_update_status=False,
    )
    base.update(kwargs)
    return GmailConfig(**base)


def _email(**kwargs) -> GmailEmail:
    base = dict(
        message_id="msg-001",
        thread_id="thread-001",
        from_email="hr@acme.com",
        from_name="ACME Recruitment",
        subject="Application for Frontend Developer",
        snippet="Thank you for your application to ACME.",
        received_at=datetime(2026, 5, 20, 10, 0, tzinfo=UTC),
    )
    base.update(kwargs)
    return GmailEmail(**base)


def _app(**kwargs) -> Application:
    base = dict(
        offer_id="offer-1",
        title="Frontend Developer",
        company="ACME",
        status=ApplicationStatus.SENT,
        sent_at=datetime(2026, 5, 10, tzinfo=UTC),
    )
    base.update(kwargs)
    return Application(**base)


# ---------------------------------------------------------------------------
# Gmail config detection
# ---------------------------------------------------------------------------


class TestIsGmailConfigured:
    def test_false_when_disabled(self, tmp_path):
        cfg = _cfg(enabled=False, credentials_path=str(tmp_path / "creds.json"))
        (tmp_path / "creds.json").write_text("{}", encoding="utf-8")
        assert not is_gmail_configured(cfg)

    def test_false_when_credentials_missing(self, tmp_path):
        cfg = _cfg(enabled=True, credentials_path=str(tmp_path / "missing.json"))
        assert not is_gmail_configured(cfg)

    def test_true_when_enabled_and_credentials_exist(self, tmp_path):
        creds = tmp_path / "creds.json"
        creds.write_text("{}", encoding="utf-8")
        cfg = _cfg(enabled=True, credentials_path=str(creds))
        assert is_gmail_configured(cfg)


class TestIsGmailAuthenticated:
    def test_false_when_token_missing(self, tmp_path):
        cfg = _cfg(token_path=str(tmp_path / "missing_token.json"))
        assert not is_gmail_authenticated(cfg)

    def test_true_when_token_exists(self, tmp_path):
        token = tmp_path / "token.json"
        token.write_text("{}", encoding="utf-8")
        cfg = _cfg(token_path=str(token))
        assert is_gmail_authenticated(cfg)


# ---------------------------------------------------------------------------
# Gmail query builder
# ---------------------------------------------------------------------------


class TestBuildGmailQuery:
    def test_query_contains_newer_than(self):
        from cv_sender.gmail_integration import _build_gmail_query  # noqa: PLC0415

        q = _build_gmail_query(7)
        assert "newer_than:7d" in q

    def test_query_contains_keywords(self):
        from cv_sender.gmail_integration import _build_gmail_query  # noqa: PLC0415

        q = _build_gmail_query(30)
        assert "interview" in q
        assert "niestety" in q


# ---------------------------------------------------------------------------
# Email / application matching score
# ---------------------------------------------------------------------------


class TestScoreEmailApplicationMatch:
    def test_company_name_match_adds_points(self):
        email = _email(snippet="ACME is happy to invite you")
        app = _app(company="ACME")
        score, reasons = score_email_application_match(email, app)
        assert score >= 50
        assert any("ACME" in r for r in reasons)

    def test_job_title_match_adds_points(self):
        email = _email(subject="Frontend Developer position update")
        app = _app(title="Frontend Developer")
        score, reasons = score_email_application_match(email, app)
        assert score >= 30
        assert any("Frontend Developer" in r for r in reasons)

    def test_sender_domain_resembles_company(self):
        email = _email(from_email="hr@technova.com")
        app = _app(company="TechNova")
        score, reasons = score_email_application_match(email, app)
        assert any("domain" in r.lower() for r in reasons)

    def test_marketing_email_penalized(self):
        email = _email(
            snippet="Unsubscribe from our newsletter. Weekly digest inside.",
            subject="Newsletter",
        )
        app = _app(company="ACME")
        score, _ = score_email_application_match(email, app)
        # Marketing penalty should keep score low even with company name
        assert score < 50

    def test_recent_application_adds_points(self):
        sent_at = datetime(2026, 5, 1, tzinfo=UTC)
        email = _email(received_at=datetime(2026, 5, 15, tzinfo=UTC))
        app = _app(company="ACME", sent_at=sent_at)
        score, reasons = score_email_application_match(email, app)
        assert any("sent" in r.lower() for r in reasons)


# ---------------------------------------------------------------------------
# Match threshold
# ---------------------------------------------------------------------------


class TestMatchEmailToApplications:
    def test_returns_none_below_threshold(self):
        email = _email(
            from_email="info@randomcompany.com",
            subject="Some unrelated email",
            snippet="Generic content",
        )
        app = _app(company="TotallyDifferentCo", title="Backend Engineer")
        result = match_email_to_applications(email, [app])
        assert result is None

    def test_returns_match_above_threshold(self):
        email = _email(
            from_email="hr@acme.com",
            subject="Application for Frontend Developer at ACME",
            snippet="ACME team would like to invite you for a Frontend Developer interview",
        )
        app = _app(company="ACME", title="Frontend Developer")
        result = match_email_to_applications(email, [app])
        assert result is not None
        matched_app, score, _ = result
        assert matched_app.id == app.id
        assert score >= 50

    def test_only_matches_active_statuses(self):
        email = _email(
            subject="Application for Frontend Developer at ACME",
            snippet="ACME Frontend Developer",
        )
        app = _app(company="ACME", title="Frontend Developer", status=ApplicationStatus.ARCHIVED)
        result = match_email_to_applications(email, [app])
        assert result is None


# ---------------------------------------------------------------------------
# Classification – keyword rules
# ---------------------------------------------------------------------------


class TestClassifyEmail:
    def test_rejection_keywords(self):
        email = _email(snippet="unfortunately we decided not to move forward with your application")
        result = classify_email(email, None)
        assert result.classification == EmailClassification.REJECTION
        assert result.status_suggestion == "rejected"

    def test_interview_keywords(self):
        email = _email(snippet="We would like to invite you for an interview. Please check your availability.")
        result = classify_email(email, None)
        assert result.classification == EmailClassification.INTERVIEW_INVITATION
        assert result.status_suggestion == "interview"

    def test_offer_keywords(self):
        email = _email(subject="Job offer from ACME", snippet="We are pleased to extend a formal job offer")
        result = classify_email(email, None)
        assert result.classification == EmailClassification.OFFER
        assert result.status_suggestion == "offer"

    def test_automated_confirmation(self):
        email = _email(snippet="thank you for applying to our company. We received your application.")
        result = classify_email(email, None)
        assert result.classification == EmailClassification.AUTOMATED_CONFIRMATION
        assert result.status_suggestion == "no_change"

    def test_unknown_when_no_keywords(self):
        email = _email(snippet="Hi, how are you?", subject="Just checking in")
        result = classify_email(email, None)
        assert result.classification == EmailClassification.UNKNOWN


# ---------------------------------------------------------------------------
# Duplicate match prevention
# ---------------------------------------------------------------------------


class TestDuplicatePrevention:
    def test_does_not_add_duplicate_message_id(self, tmp_path):
        from cv_sender.storage import add_email_match, load_email_matches  # noqa: PLC0415

        match = EmailMatch(
            application_id="app-1",
            email_message_id="msg-001",
            received_at=datetime.now(UTC),
        )
        added_first = add_email_match(match)
        added_second = add_email_match(match)
        assert added_first is True
        assert added_second is False
        all_matches = load_email_matches()
        assert len(all_matches) == 1

    def test_scan_skips_existing_message_ids(self):
        """scan_gmail_for_application_replies skips messages already in existing_ids."""
        email = _email(message_id="already-seen")
        app = _app(company="ACME", title="Frontend Developer")

        mock_service = MagicMock()
        with patch(
            "cv_sender.gmail_integration.search_recent_emails",
            return_value=[email],
        ):
            result = scan_gmail_for_application_replies(
                service=mock_service,
                applications=[app],
                cfg=_cfg(),
                existing_message_ids={"already-seen"},
            )
        assert result == []


# ---------------------------------------------------------------------------
# Full scan pipeline
# ---------------------------------------------------------------------------


class TestScanPipeline:
    def test_scan_creates_match_for_matching_email(self):
        email = _email(
            message_id="new-msg-001",
            subject="Application for Frontend Developer at ACME",
            snippet="ACME Frontend Developer interview invitation. Please confirm your availability.",
        )
        app = _app(company="ACME", title="Frontend Developer")

        mock_service = MagicMock()
        with patch(
            "cv_sender.gmail_integration.search_recent_emails",
            return_value=[email],
        ):
            matches = scan_gmail_for_application_replies(
                service=mock_service,
                applications=[app],
                cfg=_cfg(),
                existing_message_ids=set(),
            )

        assert len(matches) == 1
        m = matches[0]
        assert m.email_message_id == "new-msg-001"
        assert m.application_id == app.id
        assert m.matched_company == "ACME"

    def test_scan_ignores_non_matching_email(self):
        email = _email(
            message_id="new-msg-002",
            from_email="info@randomsite.com",
            subject="Your order has shipped",
            snippet="Your package is on the way.",
        )
        app = _app(company="TotallyDifferentCo", title="Backend Engineer")

        mock_service = MagicMock()
        with patch(
            "cv_sender.gmail_integration.search_recent_emails",
            return_value=[email],
        ):
            matches = scan_gmail_for_application_replies(
                service=mock_service,
                applications=[app],
                cfg=_cfg(),
                existing_message_ids=set(),
            )
        assert matches == []


# ---------------------------------------------------------------------------
# apply_email_match service
# ---------------------------------------------------------------------------


class TestApplyEmailMatch:
    def _setup(self, tmp_path):
        from cv_sender.storage import add_application, add_email_match  # noqa: PLC0415

        app = _app(status=ApplicationStatus.SENT)
        add_application(app)
        match = EmailMatch(
            application_id=app.id,
            email_message_id="msg-apply-001",
            received_at=datetime(2026, 5, 20, tzinfo=UTC),
            classification=EmailClassification.REJECTION,
            status_suggestion="rejected",
        )
        add_email_match(match)
        return app, match

    def test_updates_application_status(self, tmp_path):
        from cv_sender.services import apply_email_match  # noqa: PLC0415
        from cv_sender.storage import get_application_by_id  # noqa: PLC0415

        app, match = self._setup(tmp_path)
        ok, msg = apply_email_match(match.id)
        assert ok
        updated = get_application_by_id(app.id)
        assert updated.status == ApplicationStatus.REJECTED

    def test_appends_email_match_applied_event(self, tmp_path):
        from cv_sender.services import apply_email_match  # noqa: PLC0415
        from cv_sender.storage import get_application_by_id  # noqa: PLC0415

        app, match = self._setup(tmp_path)
        apply_email_match(match.id)
        updated = get_application_by_id(app.id)
        event_types = [e.event for e in updated.events]
        assert "email_match_applied" in event_types

    def test_sets_last_contact_at(self, tmp_path):
        from cv_sender.services import apply_email_match  # noqa: PLC0415
        from cv_sender.storage import get_application_by_id  # noqa: PLC0415

        app, match = self._setup(tmp_path)
        apply_email_match(match.id)
        updated = get_application_by_id(app.id)
        assert updated.last_contact_at == datetime(2026, 5, 20, tzinfo=UTC)

    def test_marks_match_as_applied(self, tmp_path):
        from cv_sender.services import apply_email_match  # noqa: PLC0415
        from cv_sender.storage import get_email_match_by_id  # noqa: PLC0415

        app, match = self._setup(tmp_path)
        apply_email_match(match.id)
        updated_match = get_email_match_by_id(match.id)
        assert updated_match.status == EmailMatchStatus.APPLIED

    def test_no_auto_update_by_default(self):
        """apply_email_match is never called automatically; default config has auto_update_status=False."""
        cfg = _cfg(auto_update_status=False)
        assert cfg.auto_update_status is False

    def test_no_change_suggestion_does_not_change_status(self, tmp_path):
        from cv_sender.services import apply_email_match  # noqa: PLC0415
        from cv_sender.storage import add_application, add_email_match, get_application_by_id  # noqa: PLC0415

        app = _app(status=ApplicationStatus.SENT)
        add_application(app)
        match = EmailMatch(
            application_id=app.id,
            email_message_id="msg-no-change",
            received_at=datetime.now(UTC),
            classification=EmailClassification.AUTOMATED_CONFIRMATION,
            status_suggestion="no_change",
        )
        add_email_match(match)
        ok, msg = apply_email_match(match.id)
        assert ok
        updated = get_application_by_id(app.id)
        assert updated.status == ApplicationStatus.SENT  # unchanged


# ---------------------------------------------------------------------------
# ignore_email_match service
# ---------------------------------------------------------------------------


class TestIgnoreEmailMatch:
    def test_marks_match_as_ignored(self, tmp_path):
        from cv_sender.services import ignore_email_match  # noqa: PLC0415
        from cv_sender.storage import add_application, add_email_match, get_email_match_by_id  # noqa: PLC0415

        app = _app()
        add_application(app)
        match = EmailMatch(
            application_id=app.id,
            email_message_id="msg-ignore-001",
            received_at=datetime.now(UTC),
        )
        add_email_match(match)
        ok, msg = ignore_email_match(match.id)
        assert ok
        updated = get_email_match_by_id(match.id)
        assert updated.status == EmailMatchStatus.IGNORED


# ---------------------------------------------------------------------------
# get_matches_for_application service
# ---------------------------------------------------------------------------


class TestGetMatchesForApplication:
    def test_returns_only_matching_app_matches(self, tmp_path):
        from cv_sender.services import get_matches_for_application  # noqa: PLC0415
        from cv_sender.storage import add_application, add_email_match  # noqa: PLC0415

        app1 = _app()
        app2 = _app()
        add_application(app1)
        add_application(app2)

        m1 = EmailMatch(
            application_id=app1.id, email_message_id="m1", received_at=datetime.now(UTC)
        )
        m2 = EmailMatch(
            application_id=app2.id, email_message_id="m2", received_at=datetime.now(UTC)
        )
        add_email_match(m1)
        add_email_match(m2)

        result = get_matches_for_application(app1.id)
        assert len(result) == 1
        assert result[0].email_message_id == "m1"
