"""Service layer – thin wrappers that keep business logic out of the UI."""

from __future__ import annotations

from datetime import UTC, datetime

from cv_sender.config import load_profile, load_settings
from cv_sender.llm import get_llm_score
from cv_sender.models import (
    Application,
    ApplicationEvent,
    ApplicationStatus,
    BatchImportItemResult,
    BatchImportResult,
    ImportStatus,
    Offer,
)
from cv_sender.scorer import score_offer
from cv_sender.storage import (
    add_application,
    add_offer,
    get_application_by_id,
    get_offer_by_id,
    load_applications,
    load_offers,
    update_application,
    update_offer,
)
from cv_sender.url_utils import infer_source, is_valid_url, normalize_url, parse_url_lines


# ---------------------------------------------------------------------------
# Offer helpers
# ---------------------------------------------------------------------------


def add_offer_manual(
    url: str,
    title: str,
    company: str = "",
    source: str = "manual",
    location: str = "",
    contract: str = "",
    salary_min: float | None = None,
    salary_max: float | None = None,
    currency: str = "PLN",
    technologies: list[str] | None = None,
    description: str = "",
) -> tuple[bool, Offer]:
    """Create an :class:`Offer` and persist it.

    Returns ``(True, offer)`` on success, ``(False, offer)`` if the URL is a
    duplicate (the returned offer is the unsaved one).
    """
    offer = Offer(
        url=url,
        title=title,
        company=company,
        source=source,
        location=location,
        contract=contract,
        salary_min=salary_min,
        salary_max=salary_max,
        currency=currency,
        technologies=technologies or [],
        description=description,
    )
    saved = add_offer(offer)
    return saved, offer


def score_offer_by_id(offer_id: str, use_llm: bool = True) -> tuple[bool, str, Offer | None]:
    """Score an offer by its *offer_id*.

    Returns ``(success, message, updated_offer)``.
    Uses LLM only when *use_llm=True* and LM Studio is enabled in settings.
    Falls back gracefully if LM Studio is unavailable.
    """
    offer = get_offer_by_id(offer_id)
    if offer is None:
        return False, f"Offer '{offer_id}' not found.", None

    settings = load_settings()

    llm_result: dict | None = None
    llm_warning: str = ""
    if use_llm and settings.lm_studio.enabled:
        llm_result = get_llm_score(
            offer_data=offer.model_dump(mode="json"),
            criteria_data=settings.model_dump(mode="json"),
            config=settings.lm_studio,
        )
        if llm_result is None:
            llm_warning = "LM Studio unavailable – using deterministic scoring only."

    updated = score_offer(offer, settings, llm_result=llm_result)
    update_offer(updated)

    msg = f"Score: {updated.score} | Decision: {updated.decision}"
    if llm_warning:
        msg += f" | ⚠ {llm_warning}"
    return True, msg, updated


def import_offer_from_url(
    url: str,
    source_override: str | None = None,
    auto_score: bool = True,
) -> BatchImportItemResult:
    """Import a single job offer from a URL.

    No HTTP requests are made – the offer is stored with the provided URL and
    a title/source derived from the URL.  Returns a
    :class:`BatchImportItemResult` describing the outcome.
    """
    norm_url = normalize_url(url)

    if not is_valid_url(norm_url):
        return BatchImportItemResult(
            url=url,
            status=ImportStatus.INVALID,
            error=f"Not a valid HTTP/HTTPS URL: {url!r}",
        )

    source = source_override or infer_source(norm_url)

    # Derive a minimal title from the URL path so the offer is identifiable.
    from urllib.parse import urlparse  # noqa: PLC0415

    path_parts = [p for p in urlparse(norm_url).path.split("/") if p]
    raw_title = path_parts[-1].replace("-", " ").replace("_", " ") if path_parts else norm_url
    title = raw_title[:120]  # cap to avoid absurdly long titles

    offer = Offer(url=norm_url, title=title, source=source)
    saved = add_offer(offer)

    if not saved:
        return BatchImportItemResult(
            url=url,
            status=ImportStatus.DUPLICATE,
            error="An offer with this URL already exists.",
        )

    # Optional scoring
    if auto_score:
        try:
            settings = load_settings()
            llm_result = None
            if settings.lm_studio.enabled:
                llm_result = get_llm_score(
                    offer_data=offer.model_dump(mode="json"),
                    criteria_data=settings.model_dump(mode="json"),
                    config=settings.lm_studio,
                )
            scored = score_offer(offer, settings, llm_result=llm_result)
            update_offer(scored)
            return BatchImportItemResult(
                url=url,
                status=ImportStatus.IMPORTED,
                offer_id=scored.id,
                company=scored.company,
                title=scored.title,
                score=scored.score,
                decision=scored.decision,
            )
        except Exception as exc:  # noqa: BLE001
            # Import succeeded; only scoring failed – report imported with error note.
            return BatchImportItemResult(
                url=url,
                status=ImportStatus.IMPORTED,
                offer_id=offer.id,
                company=offer.company,
                title=offer.title,
                error=f"Scoring failed: {exc}",
            )

    return BatchImportItemResult(
        url=url,
        status=ImportStatus.IMPORTED,
        offer_id=offer.id,
        company=offer.company,
        title=offer.title,
    )


_DEFAULT_MAX_URLS = 20
_HARD_MAX_URLS = 50


def import_offers_from_urls(
    urls: list[str],
    source_override: str | None = None,
    auto_score: bool = True,
    max_urls: int = _DEFAULT_MAX_URLS,
) -> BatchImportResult:
    """Import multiple job offers from a list of URLs.

    Processing rules:
    - *max_urls* is capped at :data:`_HARD_MAX_URLS` (50).
    - URLs beyond the limit are marked :attr:`ImportStatus.SKIPPED_LIMIT`.
    - Duplicates within the input are detected by normalized URL and marked
      :attr:`ImportStatus.DUPLICATE` without touching storage.
    - Duplicates against existing storage are also detected and marked accordingly.
    - One failed URL never stops the rest.
    """
    effective_max = min(max_urls, _HARD_MAX_URLS)

    result = BatchImportResult()

    # Pre-load existing normalized URLs for fast duplicate detection.
    existing_norm_urls: set[str] = {normalize_url(o.url) for o in load_offers()}
    # Track normalized URLs seen within this batch.
    seen_in_batch: set[str] = set()

    for i, raw_url in enumerate(urls):
        stripped = raw_url.strip()

        if i >= effective_max:
            result.items.append(
                BatchImportItemResult(
                    url=stripped,
                    status=ImportStatus.SKIPPED_LIMIT,
                    error=f"Limit of {effective_max} URLs per batch reached.",
                )
            )
            continue

        if not stripped:
            continue

        norm = normalize_url(stripped)

        # Duplicate within the current batch
        if norm in seen_in_batch:
            result.items.append(
                BatchImportItemResult(
                    url=stripped,
                    status=ImportStatus.DUPLICATE,
                    error="Duplicate within the submitted batch.",
                )
            )
            continue
        seen_in_batch.add(norm)

        # Duplicate in existing storage
        if norm in existing_norm_urls:
            result.items.append(
                BatchImportItemResult(
                    url=stripped,
                    status=ImportStatus.DUPLICATE,
                    error="An offer with this URL already exists in storage.",
                )
            )
            continue

        # Attempt single-URL import
        try:
            item = import_offer_from_url(
                stripped,
                source_override=source_override,
                auto_score=auto_score,
            )
        except Exception as exc:  # noqa: BLE001
            item = BatchImportItemResult(
                url=stripped,
                status=ImportStatus.FAILED,
                error=str(exc),
            )

        if item.status == ImportStatus.IMPORTED:
            existing_norm_urls.add(norm)

        result.items.append(item)

    return result


# ---------------------------------------------------------------------------
# Application helpers
# ---------------------------------------------------------------------------


def fill_application_for_offer(offer_id: str) -> tuple[bool, str, Application | None]:
    """Run Playwright form filling for *offer_id*.

    Returns ``(success, message, application)``.
    Creates (or updates) an :class:`Application` record regardless of outcome.
    The form is **never** auto-submitted (``wait_for_review=False`` is passed
    so the browser closes after filling; the user must manually submit from the
    CLI if interactive review is needed).
    """
    offer = get_offer_by_id(offer_id)
    if offer is None:
        return False, f"Offer '{offer_id}' not found.", None

    profile = load_profile()
    settings = load_settings()

    # Find existing application or prepare a new one
    existing = _find_application_for_offer(offer_id)

    try:
        from cv_sender.form_filler import fill_application  # noqa: PLC0415

        fill_application(offer, profile, settings, wait_for_review=False)
    except Exception as exc:  # noqa: BLE001
        error_msg = f"Browser error: {exc}"
        app = _upsert_application(
            existing=existing,
            offer=offer,
            profile_cv=profile.cv_path,
            status=ApplicationStatus.FAILED,
            event_type="fill_failed",
            event_details=error_msg,
        )
        return False, error_msg, app

    success_msg = (
        "Application form has been filled. "
        "Please review it manually before submitting."
    )
    app = _upsert_application(
        existing=existing,
        offer=offer,
        profile_cv=profile.cv_path,
        status=ApplicationStatus.READY_TO_SEND,
        event_type="form_filled",
        event_details="Form filled via cv-sender UI; awaiting manual submission.",
    )
    return True, success_msg, app


def update_application_status(
    app_id: str,
    new_status: ApplicationStatus,
) -> tuple[bool, str]:
    """Change the status of an application and append a lifecycle event."""
    app = get_application_by_id(app_id)
    if app is None:
        return False, f"Application '{app_id}' not found."

    old_status = app.status
    now = datetime.now(UTC)
    updated = app.model_copy(
        update={
            "status": new_status,
            "updated_at": now,
        }
    )
    if new_status != old_status:
        updated.events.append(
            ApplicationEvent(
                timestamp=now,
                event="status_changed",
                details=f"{old_status} → {new_status}",
            )
        )
    update_application(updated)
    return True, f"Status updated to '{new_status}'."


def update_application_notes(app_id: str, notes: str) -> tuple[bool, str]:
    """Update the free-text notes on an application."""
    app = get_application_by_id(app_id)
    if app is None:
        return False, f"Application '{app_id}' not found."

    updated = app.model_copy(
        update={
            "notes": notes,
            "updated_at": datetime.now(UTC),
        }
    )
    update_application(updated)
    return True, "Notes saved."


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_application_for_offer(offer_id: str) -> Application | None:
    """Return the most recent application for *offer_id*, or ``None``."""
    apps = load_applications()
    matches = [a for a in apps if a.offer_id == offer_id]
    if not matches:
        return None
    return max(matches, key=lambda a: a.created_at)


def _upsert_application(
    *,
    existing: Application | None,
    offer: Offer,
    profile_cv: str,
    status: ApplicationStatus,
    event_type: str,
    event_details: str,
) -> Application:
    """Create a new application or update *existing* with *status* and a new event."""
    now = datetime.now(UTC)
    new_event = ApplicationEvent(
        timestamp=now,
        event=event_type,
        details=event_details,
    )

    if existing is not None:
        updated = existing.model_copy(
            update={
                "status": status,
                "updated_at": now,
            }
        )
        updated.events.append(new_event)
        update_application(updated)
        return updated

    app = Application(
        offer_id=offer.id,
        source=offer.source,
        url=offer.url,
        company=offer.company,
        title=offer.title,
        salary_min=offer.salary_min,
        salary_max=offer.salary_max,
        currency=offer.currency,
        contract=offer.contract,
        location=offer.location,
        status=status,
        score=offer.score,
        cv_file=profile_cv,
        events=[new_event],
    )
    add_application(app)
    return app
