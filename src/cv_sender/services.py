"""Service layer – thin wrappers that keep business logic out of the UI."""

from __future__ import annotations

from datetime import UTC, datetime

from cv_sender.config import load_profile, load_settings
from cv_sender.llm import get_llm_score
from cv_sender.models import (
    Application,
    ApplicationEvent,
    ApplicationStatus,
    Offer,
)
from cv_sender.scorer import score_offer
from cv_sender.storage import (
    add_application,
    add_offer,
    get_application_by_id,
    get_offer_by_id,
    load_applications,
    update_application,
    update_offer,
)


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
