"""Gmail read-only integration for detecting recruiter/company replies.

Scope: https://www.googleapis.com/auth/gmail.readonly

Emails are never sent, deleted, archived, or modified.
Full email bodies are not stored unless settings.gmail.store_email_body is True.
OAuth credentials and tokens are never logged.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cv_sender.config import GmailConfig
from cv_sender.models import (
    Application,
    ApplicationStatus,
    EmailClassification,
    EmailMatch,
)

logger = logging.getLogger(__name__)

_GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

# ---------------------------------------------------------------------------
# Keyword tables for rule-based classification
# ---------------------------------------------------------------------------

_REJECTION_KEYWORDS = [
    "niestety",
    "nie możemy zaprosić",
    "decided not to move forward",
    "unfortunately",
    "we will not be proceeding",
    "we regret",
    "nie zakwalifikowali",
    "not selected",
    "other candidates",
    "does not match",
    "nie spełnia",
]

_INTERVIEW_KEYWORDS = [
    "rozmowa",
    "interview",
    "call",
    "spotkanie",
    "calendar",
    "availability",
    "termin",
    "zapraszamy na",
    "schedule a",
    "book a",
    "join us",
    "następny krok",
    "next step",
    "next round",
]

_OFFER_KEYWORDS = [
    "offer",
    "oferta współpracy",
    "job offer",
    "formal offer",
    "employment offer",
    "propozycja zatrudnienia",
]

_CONFIRMATION_KEYWORDS = [
    "dziękujemy za aplikację",
    "thank you for applying",
    "received your application",
    "potwierdzamy otrzymanie",
    "application received",
    "zgłoszenie zostało",
]

_RECRUITER_KEYWORDS = [
    "rekrutacja",
    "aplikacja",
    "application",
    "recruitment",
    "interview",
    "rozmowa",
    "thank you for applying",
    "dziękujemy za aplikację",
    "unfortunately",
    "niestety",
    "zapraszamy",
    "next step",
]

_MARKETING_KEYWORDS = [
    "unsubscribe",
    "newsletter",
    "promotional",
    "weekly digest",
    "zasubskrybuj",
    "wypisz się",
]


# ---------------------------------------------------------------------------
# Internal data class for a raw Gmail message
# ---------------------------------------------------------------------------


@dataclass
class GmailEmail:
    """Lightweight representation of a Gmail message."""

    message_id: str
    thread_id: str
    from_email: str
    from_name: str
    subject: str
    snippet: str
    received_at: datetime
    body: str = ""          # populated only when store_email_body=True


# ---------------------------------------------------------------------------
# EmailClassificationResult
# ---------------------------------------------------------------------------


@dataclass
class EmailClassificationResult:
    classification: EmailClassification = EmailClassification.UNKNOWN
    confidence: float = 0.0
    status_suggestion: str = "no_change"
    reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Configuration detection
# ---------------------------------------------------------------------------


def is_gmail_configured(cfg: GmailConfig | None = None) -> bool:
    """Return True if Gmail credentials exist and the feature is enabled."""
    if cfg is None:
        from cv_sender.config import load_settings  # noqa: PLC0415
        cfg = load_settings().gmail
    if not cfg.enabled:
        return False
    creds_path = Path(cfg.credentials_path)
    return creds_path.exists()


def is_gmail_authenticated(cfg: GmailConfig | None = None) -> bool:
    """Return True if a valid token file already exists (first OAuth done)."""
    if cfg is None:
        from cv_sender.config import load_settings  # noqa: PLC0415
        cfg = load_settings().gmail
    return Path(cfg.token_path).exists()


# ---------------------------------------------------------------------------
# Gmail service initialisation
# ---------------------------------------------------------------------------


def get_gmail_service(cfg: GmailConfig | None = None) -> Any:
    """Build and return an authenticated Gmail API service object.

    Raises ``ImportError`` if the Google API client library is not installed.
    Raises ``FileNotFoundError`` if the credentials file is missing.
    Raises ``RuntimeError`` for OAuth / API errors.

    The caller is responsible for catching these and presenting a graceful error.
    """
    try:
        from google.auth.transport.requests import Request  # noqa: PLC0415
        from google.oauth2.credentials import Credentials  # noqa: PLC0415
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: PLC0415
        from googleapiclient.discovery import build  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "Google API client libraries not installed. "
            "Run: pip install google-api-python-client google-auth google-auth-oauthlib google-auth-httplib2"
        ) from exc

    if cfg is None:
        from cv_sender.config import load_settings  # noqa: PLC0415
        cfg = load_settings().gmail

    creds_path = Path(cfg.credentials_path)
    token_path = Path(cfg.token_path)

    if not creds_path.exists():
        raise FileNotFoundError(
            f"Gmail credentials not found at '{cfg.credentials_path}'. "
            "Download OAuth 2.0 credentials from Google Cloud Console and place them there."
        )

    creds: Credentials | None = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), [_GMAIL_READONLY_SCOPE])

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                logger.warning("Token refresh failed: %s – re-running OAuth flow.", exc)
                creds = None
        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(creds_path), [_GMAIL_READONLY_SCOPE]
            )
            creds = flow.run_local_server(port=0)

        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return build("gmail", "v1", credentials=creds)


# ---------------------------------------------------------------------------
# Low-level fetching
# ---------------------------------------------------------------------------


def _parse_headers(headers: list[dict]) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in headers}


def _decode_from(raw: str) -> tuple[str, str]:
    """Split 'Name <email@example.com>' into (name, email)."""
    m = re.match(r"^(.*?)\s*<([^>]+)>$", raw.strip())
    if m:
        return m.group(1).strip().strip('"'), m.group(2).strip()
    return "", raw.strip()


def _parse_date(date_str: str) -> datetime:
    """Parse an RFC 2822 date header into a UTC datetime."""
    from email.utils import parsedate_to_datetime  # noqa: PLC0415

    try:
        dt = parsedate_to_datetime(date_str)
        return dt.astimezone(UTC)
    except Exception:  # noqa: BLE001
        return datetime.now(UTC)


def _build_gmail_query(days_back: int) -> str:
    keyword_query = " OR ".join(
        f'"{kw}"' for kw in [
            "rekrutacja", "aplikacja", "application", "recruitment",
            "interview", "rozmowa", "thank you for applying",
            "dziękujemy za aplikację", "unfortunately", "niestety",
            "zapraszamy", "next step",
        ]
    )
    return f"newer_than:{days_back}d ({keyword_query})"


def search_recent_emails(
    service: Any,
    cfg: GmailConfig,
) -> list[GmailEmail]:
    """Search Gmail for recent job-related messages.

    Returns a list of :class:`GmailEmail` objects.
    Full bodies are fetched only when ``cfg.store_email_body`` is True.
    """
    query = _build_gmail_query(cfg.scan_days_back)
    results: list[GmailEmail] = []

    try:
        response = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=cfg.max_results)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Gmail list request failed: %s", exc)
        return []

    messages = response.get("messages", [])

    for msg_stub in messages:
        msg_id = msg_stub["id"]
        try:
            fmt = "full" if cfg.store_email_body else "metadata"
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=msg_id, format=fmt)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch message %s: %s", msg_id, exc)
            continue

        payload = msg.get("payload", {})
        headers = _parse_headers(payload.get("headers", []))
        from_name, from_email = _decode_from(headers.get("from", ""))
        subject = headers.get("subject", "")
        date_str = headers.get("date", "")
        snippet = msg.get("snippet", "") if cfg.store_snippet else ""
        thread_id = msg.get("threadId", "")
        received_at = _parse_date(date_str)

        body = ""
        if cfg.store_email_body:
            body = _extract_body(payload)

        results.append(
            GmailEmail(
                message_id=msg_id,
                thread_id=thread_id,
                from_email=from_email,
                from_name=from_name,
                subject=subject,
                snippet=snippet,
                received_at=received_at,
                body=body,
            )
        )

    return results


def _extract_body(payload: dict) -> str:
    """Extract plain-text body from a Gmail message payload."""
    def _parts(p: dict) -> str:
        if p.get("mimeType") == "text/plain":
            data = p.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        for part in p.get("parts", []):
            result = _parts(part)
            if result:
                return result
        return ""

    return _parts(payload)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

_MATCH_THRESHOLD = 50


def _normalize(text: str) -> str:
    return text.lower().strip()


def _count_keyword_hits(text: str, keywords: list[str]) -> int:
    t = _normalize(text)
    return sum(1 for kw in keywords if kw.lower() in t)


def _sender_domain(email: str) -> str:
    """Return the registrable domain of *email* (e.g. 'acme.com')."""
    if "@" in email:
        return email.split("@", 1)[1].lower()
    return ""


def _domain_from_company(company: str) -> str:
    """Rough heuristic: lowercase, strip spaces/punctuation."""
    return re.sub(r"[^a-z0-9]", "", company.lower())


def _domain_resembles_company(sender_domain: str, company: str) -> bool:
    """True if the sender domain contains a normalised form of the company name."""
    if not sender_domain or not company:
        return False
    slug = _domain_from_company(company)
    if not slug:
        return False
    return slug in sender_domain.replace(".", "")


def score_email_application_match(
    email: GmailEmail,
    app: Application,
) -> tuple[int, list[str]]:
    """Return (score, reasons) for how well *email* matches *app*."""
    score = 0
    reasons: list[str] = []
    haystack = " ".join([
        email.from_email, email.from_name, email.subject, email.snippet, email.body
    ])

    # Company name match
    if app.company and _normalize(app.company) in _normalize(haystack):
        score += 50
        reasons.append(f"Company name '{app.company}' found in email")

    # Job title match
    if app.title and _normalize(app.title) in _normalize(haystack):
        score += 30
        reasons.append(f"Job title '{app.title}' found in email")

    # Sender domain resembles company
    domain = _sender_domain(email.from_email)
    if _domain_resembles_company(domain, app.company):
        score += 20
        reasons.append(f"Sender domain '{domain}' resembles company '{app.company}'")

    # Application is recent (within last 45 days)
    if app.sent_at:
        days_since = (email.received_at - app.sent_at).days
        if 0 <= days_since <= 45:
            score += 15
            reasons.append(f"Application sent {days_since} days before email")

    # Recruiter keywords
    hits = _count_keyword_hits(haystack, _RECRUITER_KEYWORDS)
    if hits >= 2:
        score += 10
        reasons.append(f"{hits} recruiter keywords found")

    # Marketing penalty
    if _count_keyword_hits(haystack, _MARKETING_KEYWORDS) >= 2:
        score -= 50
        reasons.append("Marketing/newsletter email detected")

    return score, reasons


def match_email_to_applications(
    email: GmailEmail,
    applications: list[Application],
) -> tuple[Application, int, list[str]] | None:
    """Return (best_application, score, reasons) or None if no match above threshold."""
    best_app: Application | None = None
    best_score = _MATCH_THRESHOLD - 1
    best_reasons: list[str] = []

    # Only consider sent/follow-up applications
    _active = {
        ApplicationStatus.SENT,
        ApplicationStatus.FOLLOW_UP_DUE,
        ApplicationStatus.FOLLOW_UP_SENT,
        ApplicationStatus.REPLY_RECEIVED,
        ApplicationStatus.INTERVIEW,
    }
    candidates = [a for a in applications if a.status in _active]

    for app in candidates:
        s, reasons = score_email_application_match(email, app)
        if s > best_score:
            best_score = s
            best_app = app
            best_reasons = reasons

    if best_app is None:
        return None
    return best_app, best_score, best_reasons


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _keyword_classify(text: str) -> EmailClassificationResult:
    t = _normalize(text)
    if _count_keyword_hits(t, _REJECTION_KEYWORDS) >= 1:
        return EmailClassificationResult(
            classification=EmailClassification.REJECTION,
            confidence=0.85,
            status_suggestion="rejected",
            reasons=["Rejection keywords found"],
        )
    if _count_keyword_hits(t, _OFFER_KEYWORDS) >= 1:
        return EmailClassificationResult(
            classification=EmailClassification.OFFER,
            confidence=0.80,
            status_suggestion="offer",
            reasons=["Offer keywords found"],
        )
    if _count_keyword_hits(t, _INTERVIEW_KEYWORDS) >= 2:
        return EmailClassificationResult(
            classification=EmailClassification.INTERVIEW_INVITATION,
            confidence=0.80,
            status_suggestion="interview",
            reasons=["Interview keywords found"],
        )
    if _count_keyword_hits(t, _CONFIRMATION_KEYWORDS) >= 1:
        return EmailClassificationResult(
            classification=EmailClassification.AUTOMATED_CONFIRMATION,
            confidence=0.75,
            status_suggestion="no_change",
            reasons=["Auto-confirmation keywords found"],
        )
    if _count_keyword_hits(t, _RECRUITER_KEYWORDS) >= 2:
        return EmailClassificationResult(
            classification=EmailClassification.REPLY_RECEIVED,
            confidence=0.60,
            status_suggestion="reply_received",
            reasons=["Recruiter/reply keywords found"],
        )
    return EmailClassificationResult(
        classification=EmailClassification.UNKNOWN,
        confidence=0.3,
        status_suggestion="no_change",
        reasons=["No strong keywords matched"],
    )


def _llm_classify(
    email: GmailEmail,
    application: Application | None,
    store_body: bool = False,
) -> EmailClassificationResult | None:
    """Try to classify via LM Studio. Returns None if LLM is unavailable."""
    try:
        from openai import OpenAI  # noqa: PLC0415

        from cv_sender.config import load_settings  # noqa: PLC0415
        cfg = load_settings().lm_studio
        if not cfg.enabled:
            return None

        text_to_analyse = email.snippet
        if store_body and email.body:
            text_to_analyse = email.body[:1000]

        context = ""
        if application:
            context = (
                f"Applied to: {application.title} at {application.company}. "
                f"Status: {application.status}."
            )

        prompt = (
            "Classify the following email related to a job application.\n"
            f"Context: {context}\n"
            f"Email subject: {email.subject}\n"
            f"From: {email.from_name} <{email.from_email}>\n"
            f"Snippet: {text_to_analyse}\n\n"
            "Return ONLY valid JSON (no markdown):\n"
            '{"classification":"reply_received|interview_invitation|rejection|offer|'
            'recruiter_screening|automated_confirmation|unrelated|unknown",'
            '"confidence":0.0,'
            '"status_suggestion":"reply_received|interview|rejected|offer|no_change",'
            '"reasons":[]}'
        )

        client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)
        response = client.chat.completions.create(
            model=cfg.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        content = response.choices[0].message.content or ""
        # Extract JSON
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group())
        return EmailClassificationResult(
            classification=EmailClassification(data.get("classification", "unknown")),
            confidence=float(data.get("confidence", 0.5)),
            status_suggestion=data.get("status_suggestion", "no_change"),
            reasons=data.get("reasons", []),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM classification failed: %s", exc)
        return None


def classify_email(
    email: GmailEmail,
    application: Application | None,
    use_llm: bool = False,
    store_body: bool = False,
) -> EmailClassificationResult:
    """Return a classification for *email*.

    Uses keyword rules first; falls back to LM Studio when *use_llm* is True.
    """
    text = " ".join([email.subject, email.snippet, email.body])
    result = _keyword_classify(text)

    if use_llm and result.classification in (
        EmailClassification.UNKNOWN,
        EmailClassification.REPLY_RECEIVED,
    ):
        llm_result = _llm_classify(email, application, store_body=store_body)
        if llm_result and llm_result.confidence > result.confidence:
            return llm_result

    return result


# ---------------------------------------------------------------------------
# Full scan pipeline
# ---------------------------------------------------------------------------


def scan_gmail_for_application_replies(
    service: Any,
    applications: list[Application],
    cfg: GmailConfig,
    existing_message_ids: set[str] | None = None,
    use_llm: bool = False,
) -> list[EmailMatch]:
    """Scan Gmail and return new :class:`EmailMatch` objects.

    *existing_message_ids* prevents duplicate matches.
    Auto-applies suggestions only if ``cfg.auto_update_status`` is True,
    which defaults to False.
    """
    emails = search_recent_emails(service, cfg)
    existing = existing_message_ids or set()
    matches: list[EmailMatch] = []

    for email in emails:
        if email.message_id in existing:
            continue  # already matched previously

        result = match_email_to_applications(email, applications)
        if result is None:
            continue

        app, match_score, reasons = result
        classification_result = classify_email(
            email, app, use_llm=use_llm, store_body=cfg.store_email_body
        )

        snippet = email.snippet if cfg.store_snippet else ""
        match = EmailMatch(
            application_id=app.id,
            email_message_id=email.message_id,
            thread_id=email.thread_id,
            from_email=email.from_email,
            from_name=email.from_name,
            subject=email.subject,
            snippet=snippet,
            received_at=email.received_at,
            matched_company=app.company,
            matched_application_title=app.title,
            match_score=match_score,
            classification=classification_result.classification,
            confidence=classification_result.confidence,
            reasons=reasons + classification_result.reasons,
            status_suggestion=classification_result.status_suggestion,
        )
        matches.append(match)

    return matches
