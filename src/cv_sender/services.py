"""Service layer – thin wrappers that keep business logic out of the UI."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cv_sender.config import load_profile, load_settings
from cv_sender.cv_profiles import CVProfile, CVSelectionResult, load_cv_profiles, select_cv_for_offer_object, validate_cv_profiles as _validate_cv_profiles
from cv_sender.llm import get_llm_score
from cv_sender.models import (
    Application,
    ApplicationEvent,
    ApplicationStatus,
    BatchImportItemResult,
    BatchImportResult,
    EmailMatchStatus,
    FillResult,
    FillStatus,
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
from cv_sender.title_utils import normalize_company_name, normalize_offer_title
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
    normalized_title = normalize_offer_title(title, source=source, url=url)
    offer = Offer(
        url=url,
        title=normalized_title or title,
        company=normalize_company_name(company),
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

    Fetches the page HTML and runs the best source-specific extractor to
    populate offer fields (title, company, salary, etc.).  Falls back to
    URL-derived data when fetching fails or returns no useful content.
    Returns a :class:`BatchImportItemResult` describing the outcome.
    """
    from urllib.parse import urlparse  # noqa: PLC0415

    norm_url = normalize_url(url)

    if not is_valid_url(norm_url):
        return BatchImportItemResult(
            url=url,
            status=ImportStatus.INVALID,
            error=f"Not a valid HTTP/HTTPS URL: {url!r}",
        )

    source = source_override or infer_source(norm_url)

    # Extract offer fields from the page (HTML fetch + source-specific parsing).
    # Returns an empty OfferDraft when the page is unreachable or unrecognised.
    from cv_sender.extractors import extract_offer as _extract_offer  # noqa: PLC0415

    try:
        draft = _extract_offer(norm_url)
    except Exception:  # noqa: BLE001
        draft = None  # type: ignore[assignment]

    draft_title = draft.title if draft else ""
    title = normalize_offer_title(draft_title, source=source, url=norm_url)
    title_inferred_from_url = not draft_title.strip() and bool(title)
    if not title:
        path_parts = [p for p in urlparse(norm_url).path.split("/") if p]
        raw_title = path_parts[-1].replace("-", " ").replace("_", " ") if path_parts else norm_url
        title = normalize_offer_title(raw_title, source=source, url=norm_url)
        title_inferred_from_url = True

    company = normalize_company_name(draft.company if draft else "")
    warnings = list(draft.extraction_warnings if draft else [])
    extraction_source = draft.extraction_source if draft else ""
    extraction_confidence = draft.extraction_confidence if draft else 0.0
    if title_inferred_from_url:
        extraction_source = f"{extraction_source or 'unknown'}+url_slug_fallback"
        extraction_confidence = min(extraction_confidence or 0.0, 0.25)
        warnings.append("Title was inferred from URL slug; verify offer details.")
    if not company:
        warnings.append("Company was not detected.")

    offer = Offer(
        url=norm_url,
        title=title,
        source=source,
        company=company,
        location=draft.location if draft else "",
        contract=draft.contract if draft else "",
        salary_min=draft.salary_min if draft else None,
        salary_max=draft.salary_max if draft else None,
        currency=draft.currency if draft else "PLN",
        technologies=draft.technologies if draft else [],
        description=draft.description if draft else "",
        extraction_source=extraction_source,
        extraction_confidence=extraction_confidence,
        extraction_warnings=warnings,
    )
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


def re_normalize_offers() -> tuple[int, int]:
    """Normalize stored offer titles and companies in place.

    Returns ``(total_offers, changed_offers)``.
    """

    offers = load_offers()
    changed = 0
    updated_offers: list[Offer] = []

    for offer in offers:
        normalized_title = normalize_offer_title(offer.title, source=offer.source, url=offer.url)
        normalized_company = normalize_company_name(offer.company)
        updated = offer.model_copy(update={"title": normalized_title or offer.title, "company": normalized_company})
        if updated.title != offer.title or updated.company != offer.company:
            changed += 1
        updated_offers.append(updated)

    if changed:
        update_offer_map = {o.id: o for o in updated_offers}
        save_ordered = [update_offer_map[o.id] for o in offers]
        from cv_sender.storage import save_offers as _save_offers  # noqa: PLC0415

        _save_offers(save_ordered)

    return len(offers), changed


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


# ---------------------------------------------------------------------------
# CV profile service helpers
# ---------------------------------------------------------------------------


def list_cv_profiles() -> list[CVProfile]:
    """Return all configured :class:`CVProfile` objects.

    When no ``cv_profiles`` are configured, falls back to a synthetic profile
    built from ``profile.cv_path`` (backward-compatible behaviour).
    """
    return load_cv_profiles(load_profile())


def get_default_cv_profile() -> CVProfile | None:
    """Return the CV profile with ``id == profile.default_cv_id``, or the first one."""
    profile = load_profile()
    profiles = load_cv_profiles(profile)
    if not profiles:
        return None
    if profile.default_cv_id:
        found = next((cv for cv in profiles if cv.id == profile.default_cv_id), None)
        if found:
            return found
    return profiles[0]


def select_cv_for_offer(offer_id: str) -> CVSelectionResult:
    """Select the best CV for *offer_id* using deterministic scoring."""
    offer = get_offer_by_id(offer_id)
    if offer is None:
        return CVSelectionResult(
            warnings=[f"Offer '{offer_id}' not found."]
        )
    return select_cv_for_offer_object_svc(offer)


def select_cv_for_offer_object_svc(offer: Offer) -> CVSelectionResult:
    """Select the best CV for *offer* using deterministic scoring."""
    profile = load_profile()
    profiles = load_cv_profiles(profile)
    return select_cv_for_offer_object(offer, profiles, default_cv_id=profile.default_cv_id)


def validate_cv_profiles() -> list[str]:
    """Return a list of warning strings for misconfigured CV profiles."""
    profile = load_profile()
    profiles = load_cv_profiles(profile)
    return _validate_cv_profiles(profiles)


def fill_application_form(
    offer_id: str,
    *,
    auto_submit: bool = False,
    selected_cv_id: str = "",
) -> FillResult:
    """Fill the application form for *offer_id* using a source-specific filler.

    Returns a :class:`FillResult` describing which fields were filled, any
    warnings, and the overall status (``filled`` / ``partial`` / ``failed``).

    Pass *selected_cv_id* to override automatic CV selection.  When empty,
    the best matching CV is chosen automatically via :func:`select_cv_for_offer`.

    The form is **never** submitted automatically.  ``auto_submit`` must remain
    ``False``; passing ``True`` has no effect on the current implementation.
    """
    offer = get_offer_by_id(offer_id)
    if offer is None:
        return FillResult(
            status=FillStatus.FAILED,
            offer_id=offer_id,
            error=f"Offer '{offer_id}' not found.",
        )

    profile = load_profile()
    settings = load_settings()
    existing = _find_application_for_offer(offer_id)

    # CV selection
    profiles = load_cv_profiles(profile)
    if selected_cv_id:
        cv_sel = next((cv for cv in profiles if cv.id == selected_cv_id), None)
        if cv_sel is None:
            cv_sel_result = CVSelectionResult(
                selected_cv_id=selected_cv_id,
                warnings=[f"CV profile '{selected_cv_id}' not found; falling back to auto-select."],
            )
            cv_sel_result = select_cv_for_offer_object(offer, profiles, default_cv_id=profile.default_cv_id)
        else:
            cv_sel_result = CVSelectionResult(
                selected_cv_id=cv_sel.id,
                selected_cv_name=cv_sel.name or cv_sel.id,
                selected_cv_path=cv_sel.path,
                score=cv_sel.priority,
                reasons=["Manually selected"],
            )
    else:
        cv_sel_result = select_cv_for_offer_object(offer, profiles, default_cv_id=profile.default_cv_id)

    cv_path_override = cv_sel_result.selected_cv_path

    try:
        from cv_sender.form_filler import fill_application_with_result  # noqa: PLC0415

        result = fill_application_with_result(
            offer,
            profile,
            settings,
            auto_submit=auto_submit,
            cv_path_override=cv_path_override,
        )
    except Exception as exc:  # noqa: BLE001
        result = FillResult(
            status=FillStatus.FAILED,
            offer_id=offer_id,
            url=offer.url,
            error=f"Browser error: {exc}",
        )

    # Persist application record with fill outcome
    app_status = (
        ApplicationStatus.READY_TO_SEND
        if result.status != FillStatus.FAILED
        else ApplicationStatus.FAILED
    )
    event_type = "form_filled" if result.status != FillStatus.FAILED else "fill_failed"

    detail_parts: list[str] = []
    if cv_sel_result.selected_cv_name:
        detail_parts.append(f"CV: {cv_sel_result.selected_cv_name}")
    if result.fields_filled:
        detail_parts.append(f"Filled: {', '.join(result.fields_filled)}")
    if result.fields_missing:
        detail_parts.append(f"Missing: {', '.join(result.fields_missing)}")
    if result.warnings:
        detail_parts.append(f"Warnings: {'; '.join(result.warnings)}")
    if result.error:
        detail_parts.append(f"Error: {result.error}")
    event_details = " | ".join(detail_parts) or f"Status: {result.status}"

    _upsert_application(
        existing=existing,
        offer=offer,
        profile_cv=cv_path_override or profile.cv_path,
        selected_cv_id=cv_sel_result.selected_cv_id,
        selected_cv_name=cv_sel_result.selected_cv_name,
        selected_cv_path=cv_sel_result.selected_cv_path,
        status=app_status,
        event_type=event_type,
        event_details=event_details,
    )

    return result


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
    selected_cv_id: str = "",
    selected_cv_name: str = "",
    selected_cv_path: str = "",
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
        update_dict: dict = {
            "status": status,
            "updated_at": now,
        }
        if selected_cv_id:
            update_dict["selected_cv_id"] = selected_cv_id
        if selected_cv_name:
            update_dict["selected_cv_name"] = selected_cv_name
        if selected_cv_path:
            update_dict["selected_cv_path"] = selected_cv_path
        updated = existing.model_copy(update=update_dict)
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
        selected_cv_id=selected_cv_id,
        selected_cv_name=selected_cv_name,
        selected_cv_path=selected_cv_path,
        events=[new_event],
    )
    add_application(app)
    return app


# ---------------------------------------------------------------------------
# Debug run helpers
# ---------------------------------------------------------------------------


def get_debug_runs(limit: int = 50) -> list:
    """Return up to *limit* recent debug runs, newest-first."""
    from cv_sender.form_debug import load_debug_runs  # noqa: PLC0415

    return load_debug_runs(limit=limit)


def get_debug_run(run_id: str) -> object | None:
    """Load a single debug run by *run_id*."""
    from cv_sender.form_debug import load_debug_run  # noqa: PLC0415

    return load_debug_run(run_id)


def get_debug_step_log(run_id: str) -> list[dict]:
    """Load step log entries for *run_id*."""
    from cv_sender.form_debug import load_step_log  # noqa: PLC0415

    return load_step_log(run_id)


def get_debug_form_snapshot(run_id: str) -> list[dict]:
    """Load form snapshot for *run_id*."""
    from cv_sender.form_debug import load_form_snapshot  # noqa: PLC0415

    return load_form_snapshot(run_id)


def fill_application_form_retry(
    offer_id: str,
    *,
    force_generic: bool = False,
) -> "FillResult":
    """Re-run form filling for *offer_id*.

    When *force_generic* is ``True``, the :class:`GenericFiller` is used
    regardless of the offer's source URL, creating a new debug run.
    ``auto_submit`` is always ``False``.
    """
    offer = get_offer_by_id(offer_id)
    if offer is None:
        return FillResult(
            status=FillStatus.FAILED,
            offer_id=offer_id,
            error=f"Offer '{offer_id}' not found.",
        )

    profile = load_profile()
    settings = load_settings()
    existing = _find_application_for_offer(offer_id)

    # Reuse last known CV selection from existing application if available
    cv_path_override = (existing.selected_cv_path if existing and existing.selected_cv_path else "") or ""
    if not cv_path_override:
        profiles = load_cv_profiles(profile)
        cv_sel_result = select_cv_for_offer_object(offer, profiles, default_cv_id=profile.default_cv_id)
        cv_path_override = cv_sel_result.selected_cv_path
    else:
        cv_sel_result = CVSelectionResult(
            selected_cv_id=existing.selected_cv_id if existing else "",
            selected_cv_name=existing.selected_cv_name if existing else "",
            selected_cv_path=cv_path_override,
        )

    try:
        if force_generic:
            from cv_sender.portals.generic import GenericFiller  # noqa: PLC0415

            filler = GenericFiller(profile=profile, settings=settings, cv_path_override=cv_path_override)
            result = filler.fill(offer, auto_submit=False)
        else:
            from cv_sender.form_filler import fill_application_with_result  # noqa: PLC0415

            result = fill_application_with_result(offer, profile, settings, auto_submit=False, cv_path_override=cv_path_override)
    except Exception as exc:  # noqa: BLE001
        result = FillResult(
            status=FillStatus.FAILED,
            offer_id=offer_id,
            url=offer.url,
            error=f"Browser error: {exc}",
        )

    app_status = (
        ApplicationStatus.READY_TO_SEND
        if result.status != FillStatus.FAILED
        else ApplicationStatus.FAILED
    )
    event_type = "form_filled_retry" if result.status != FillStatus.FAILED else "fill_failed_retry"
    detail_parts: list[str] = []
    if force_generic:
        detail_parts.append("via GenericFiller")
    if cv_sel_result.selected_cv_name:
        detail_parts.append(f"CV: {cv_sel_result.selected_cv_name}")
    if result.fields_filled:
        detail_parts.append(f"Filled: {', '.join(result.fields_filled)}")
    if result.error:
        detail_parts.append(f"Error: {result.error}")
    event_details = " | ".join(detail_parts) or f"Status: {result.status}"

    _upsert_application(
        existing=existing,
        offer=offer,
        profile_cv=cv_path_override or profile.cv_path,
        selected_cv_id=cv_sel_result.selected_cv_id,
        selected_cv_name=cv_sel_result.selected_cv_name,
        selected_cv_path=cv_sel_result.selected_cv_path,
        status=app_status,
        event_type=event_type,
        event_details=event_details,
    )

    return result


# ---------------------------------------------------------------------------
# Answer generation
# ---------------------------------------------------------------------------

_PREVIEW_QUESTIONS = [
    "Dlaczego chcesz pracować w tej firmie?",
    "Opisz swoje doświadczenie z technologiami wymaganymi w ofercie.",
    "Jakie są Twoje oczekiwania finansowe?",
    "Od kiedy możesz zacząć?",
]


def preview_application_answers(
    offer_id: str,
    questions: list[str] | None = None,
) -> list:
    """Generate sample answers for *offer_id* without opening a browser.

    Returns a list of :class:`~cv_sender.answers.GeneratedAnswer` objects.
    Returns an empty list when the offer is not found or answer generation
    is disabled.
    """
    from cv_sender.answers import generate_answers_for_form_questions  # noqa: PLC0415

    offer = get_offer_by_id(offer_id)
    if offer is None:
        return []

    settings = load_settings()
    if not settings.answers.enabled:
        return []

    profile = load_profile()
    qs = questions or _PREVIEW_QUESTIONS
    return generate_answers_for_form_questions(
        qs,
        offer,
        settings.answer_profile,
        None,
        settings.answer_templates,
        settings.answers,
        settings.lm_studio if settings.answers.use_llm else None,
        settings_techs=list(settings.technologies),
        availability_override=profile.availability,
        notice_period_override=profile.notice_period,
    )


# ---------------------------------------------------------------------------
# Follow-up tracking
# ---------------------------------------------------------------------------


def mark_application_sent(
    app_id: str,
    sent_at: datetime | None = None,
) -> tuple[bool, str]:
    """Set *app* status to SENT and schedule a follow-up reminder.

    If *sent_at* is None, ``datetime.now(UTC)`` is used.
    Idempotent: calling again updates the timestamp and reschedules the
    follow-up, but does not create duplicate events.
    """
    from cv_sender.follow_up import calculate_follow_up_due  # noqa: PLC0415

    app = get_application_by_id(app_id)
    if app is None:
        return False, f"Application '{app_id}' not found."

    now = datetime.now(UTC)
    _sent_at = sent_at or now
    settings = load_settings()
    fu_due = calculate_follow_up_due(_sent_at, settings.follow_up)

    changes: dict = {
        "status": ApplicationStatus.SENT,
        "sent_at": _sent_at,
        "last_contact_at": _sent_at,
        "follow_up_due_at": fu_due,
        "next_action_at": fu_due,
        "next_action_type": "follow_up",
        "updated_at": now,
    }
    updated = app.model_copy(update=changes)
    updated.events.append(
        ApplicationEvent(
            timestamp=now,
            event="status_changed",
            details=f"{app.status} → {ApplicationStatus.SENT}",
        )
    )
    updated.events.append(
        ApplicationEvent(
            timestamp=now,
            event="follow_up_due_created",
            details=f"Follow-up due at {fu_due.date().isoformat()}",
        )
    )
    update_application(updated)
    return True, f"Marked as sent. Follow-up due {fu_due.date().isoformat()}."


def mark_follow_up_sent(app_id: str) -> tuple[bool, str]:
    """Record that a manual follow-up was sent."""
    app = get_application_by_id(app_id)
    if app is None:
        return False, f"Application '{app_id}' not found."

    now = datetime.now(UTC)
    updated = app.model_copy(
        update={
            "status": ApplicationStatus.FOLLOW_UP_SENT,
            "follow_up_sent_at": now,
            "last_contact_at": now,
            "next_action_at": None,
            "next_action_type": "",
            "updated_at": now,
        }
    )
    updated.events.append(
        ApplicationEvent(
            timestamp=now,
            event="follow_up_sent",
            details="Manual follow-up sent",
        )
    )
    update_application(updated)
    return True, "Follow-up marked as sent."


def mark_reply_received(app_id: str, note: str = "") -> tuple[bool, str]:
    """Record that a reply was received from the company."""
    app = get_application_by_id(app_id)
    if app is None:
        return False, f"Application '{app_id}' not found."

    now = datetime.now(UTC)
    updated = app.model_copy(
        update={
            "status": ApplicationStatus.REPLY_RECEIVED,
            "last_contact_at": now,
            "follow_up_due_at": None,
            "next_action_at": None,
            "next_action_type": "",
            "updated_at": now,
        }
    )
    updated.events.append(
        ApplicationEvent(
            timestamp=now,
            event="reply_received",
            details=note or "Reply received",
        )
    )
    update_application(updated)
    return True, "Reply received recorded."


def schedule_interview(
    app_id: str,
    interview_at: datetime,
    note: str | None = None,
) -> tuple[bool, str]:
    """Set the interview date and update status to INTERVIEW."""
    app = get_application_by_id(app_id)
    if app is None:
        return False, f"Application '{app_id}' not found."

    now = datetime.now(UTC)
    updated = app.model_copy(
        update={
            "status": ApplicationStatus.INTERVIEW,
            "interview_at": interview_at,
            "next_action_at": interview_at,
            "next_action_type": "interview",
            "next_action_note": note or "",
            "updated_at": now,
        }
    )
    updated.events.append(
        ApplicationEvent(
            timestamp=now,
            event="interview_scheduled",
            details=f"Interview at {interview_at.isoformat()}" + (f" — {note}" if note else ""),
        )
    )
    update_application(updated)
    return True, f"Interview scheduled for {interview_at.date().isoformat()}."


def snooze_application_reminder(app_id: str, days: int) -> tuple[bool, str]:
    """Delay the reminder for *app_id* by *days* days."""
    app = get_application_by_id(app_id)
    if app is None:
        return False, f"Application '{app_id}' not found."

    now = datetime.now(UTC)
    snooze_until = now + timedelta(days=days)
    updated = app.model_copy(
        update={
            "reminder_snoozed_until": snooze_until,
            "updated_at": now,
        }
    )
    updated.events.append(
        ApplicationEvent(
            timestamp=now,
            event="reminder_snoozed",
            details=f"Snoozed {days} day(s) until {snooze_until.date().isoformat()}",
        )
    )
    update_application(updated)
    return True, f"Reminder snoozed until {snooze_until.date().isoformat()}."


def update_next_action(
    app_id: str,
    next_action_at: datetime,
    next_action_type: str,
    note: str | None = None,
) -> tuple[bool, str]:
    """Set the next scheduled action for *app_id*."""
    app = get_application_by_id(app_id)
    if app is None:
        return False, f"Application '{app_id}' not found."

    now = datetime.now(UTC)
    updated = app.model_copy(
        update={
            "next_action_at": next_action_at,
            "next_action_type": next_action_type,
            "next_action_note": note or "",
            "updated_at": now,
        }
    )
    update_application(updated)
    return True, "Next action updated."


def archive_application(app_id: str, note: str = "") -> tuple[bool, str]:
    """Archive an application (sets status to ARCHIVED)."""
    app = get_application_by_id(app_id)
    if app is None:
        return False, f"Application '{app_id}' not found."

    now = datetime.now(UTC)
    updated = app.model_copy(
        update={
            "status": ApplicationStatus.ARCHIVED,
            "updated_at": now,
        }
    )
    updated.events.append(
        ApplicationEvent(
            timestamp=now,
            event="archived",
            details=note or "Application archived",
        )
    )
    update_application(updated)
    return True, "Application archived."


def get_follow_up_due_applications(
    now: datetime | None = None,
) -> list[Application]:
    """Return applications with a follow-up that is currently due.

    Excludes applications that have been snoozed past *now*.
    """
    from cv_sender.follow_up import is_follow_up_due  # noqa: PLC0415

    _now = now or datetime.now(UTC)
    apps = load_applications()
    return [a for a in apps if is_follow_up_due(a, _now)]


def get_stale_applications(now: datetime | None = None) -> list[Application]:
    """Return applications with no contact for ``mark_no_response_after_days``."""
    from cv_sender.follow_up import is_stale  # noqa: PLC0415

    _now = now or datetime.now(UTC)
    settings = load_settings()
    apps = load_applications()
    return [a for a in apps if is_stale(a, settings.follow_up, _now)]


def generate_follow_up_message_for(app_id: str) -> str:
    """Return a plain-text follow-up message for the application *app_id*.

    Returns an empty string when the application is not found.
    The message is meant to be copied and sent manually.
    """
    from cv_sender.follow_up import generate_follow_up_message  # noqa: PLC0415

    app = get_application_by_id(app_id)
    if app is None:
        return ""
    profile = load_profile()
    return generate_follow_up_message(app, candidate_name=profile.full_name)


# ---------------------------------------------------------------------------
# Gmail integration
# ---------------------------------------------------------------------------


def run_gmail_scan(use_llm: bool = False) -> tuple[int, int, str]:
    """Scan Gmail for application replies and persist new matches.

    Returns ``(new_matches, skipped_duplicates, error_message)``.
    ``error_message`` is empty on success.
    """
    from cv_sender.gmail_integration import (  # noqa: PLC0415
        get_gmail_service,
        scan_gmail_for_application_replies,
    )
    from cv_sender.storage import (  # noqa: PLC0415
        add_email_match,
        load_email_matches,
    )

    settings = load_settings()
    cfg = settings.gmail

    try:
        service = get_gmail_service(cfg)
    except (ImportError, FileNotFoundError, RuntimeError) as exc:
        return 0, 0, str(exc)

    applications = load_applications()
    existing_ids = {m.email_message_id for m in load_email_matches()}

    try:
        new_matches = scan_gmail_for_application_replies(
            service=service,
            applications=applications,
            cfg=cfg,
            existing_message_ids=existing_ids,
            use_llm=use_llm,
        )
    except Exception as exc:  # noqa: BLE001
        return 0, 0, f"Scan failed: {exc}"

    added = 0
    skipped = 0
    for match in new_matches:
        if add_email_match(match):
            added += 1
        else:
            skipped += 1

    return added, skipped, ""


def apply_email_match(match_id: str) -> tuple[bool, str]:
    """Apply the status suggestion from an email match to its application.

    This is the **only** place an email match can trigger an application update.
    ``auto_update_status`` must be explicitly True or the user must call this
    function manually (via the UI "Apply suggestion" button).

    Returns ``(success, message)``.
    """
    from cv_sender.storage import (  # noqa: PLC0415
        get_email_match_by_id,
        update_email_match,
    )

    match = get_email_match_by_id(match_id)
    if match is None:
        return False, f"Email match '{match_id}' not found."

    app = get_application_by_id(match.application_id)
    if app is None:
        return False, f"Application '{match.application_id}' not found."

    suggestion = match.status_suggestion
    _suggestion_to_status: dict[str, ApplicationStatus] = {
        "reply_received": ApplicationStatus.REPLY_RECEIVED,
        "interview": ApplicationStatus.INTERVIEW,
        "rejected": ApplicationStatus.REJECTED,
        "offer": ApplicationStatus.OFFER,
    }

    if suggestion == "no_change" or suggestion not in _suggestion_to_status:
        # Just mark the match as applied without changing status
        updated_match = match.model_copy(update={"status": EmailMatchStatus.APPLIED})
        update_email_match(updated_match)
        return True, "Match acknowledged; no status change applied."

    new_status = _suggestion_to_status[suggestion]
    now = datetime.now(UTC)

    updated_app = app.model_copy(
        update={
            "status": new_status,
            "updated_at": now,
            "last_contact_at": match.received_at,
            "events": app.events + [
                ApplicationEvent(
                    timestamp=now,
                    event="email_match_applied",
                    details=(
                        f"subject={match.subject!r} "
                        f"classification={match.classification} "
                        f"suggestion={suggestion}"
                    ),
                )
            ],
        }
    )

    # Extra fields for specific transitions
    if new_status == ApplicationStatus.INTERVIEW and app.interview_at is None:
        updated_app = updated_app.model_copy(
            update={"next_action_type": "interview", "next_action_at": match.received_at}
        )
    elif new_status in (ApplicationStatus.REJECTED, ApplicationStatus.OFFER):
        updated_app = updated_app.model_copy(
            update={"next_action_type": "", "next_action_at": None}
        )

    update_application(updated_app)

    updated_match = match.model_copy(update={"status": EmailMatchStatus.APPLIED})
    update_email_match(updated_match)

    return True, f"Application status updated to '{new_status}'."


def ignore_email_match(match_id: str) -> tuple[bool, str]:
    """Mark an email match as ignored (no status change)."""
    from cv_sender.storage import (  # noqa: PLC0415
        get_email_match_by_id,
        update_email_match,
    )

    match = get_email_match_by_id(match_id)
    if match is None:
        return False, f"Email match '{match_id}' not found."
    updated = match.model_copy(update={"status": EmailMatchStatus.IGNORED})
    update_email_match(updated)
    return True, "Match ignored."


def get_matches_for_application(app_id: str) -> list:
    """Return all email matches for the given application, sorted newest first."""
    from cv_sender.storage import load_email_matches  # noqa: PLC0415

    matches = load_email_matches()
    app_matches = [m for m in matches if m.application_id == app_id]
    return sorted(app_matches, key=lambda m: m.received_at, reverse=True)


# ---------------------------------------------------------------------------
# Interview helpers (thin re-exports from interviews module)
# ---------------------------------------------------------------------------


def create_interview(
    application_id: str,
    interview_data: dict,
    create_calendar_event: bool = False,
):
    from cv_sender.interviews import create_interview as _create  # noqa: PLC0415
    return _create(application_id, interview_data, create_calendar_event)


def schedule_interview_from_email_match(
    match_id: str,
    interview_data: dict,
    create_calendar_event: bool = False,
):
    from cv_sender.interviews import schedule_interview_from_email_match as _schedule  # noqa: PLC0415
    return _schedule(match_id, interview_data, create_calendar_event)


def list_upcoming_interviews(now=None):
    from cv_sender.interviews import list_upcoming_interviews as _list  # noqa: PLC0415
    return _list(now)


def list_past_interviews(now=None):
    from cv_sender.interviews import list_past_interviews as _list  # noqa: PLC0415
    return _list(now)


def mark_interview_completed(interview_id: str):
    from cv_sender.interviews import mark_interview_completed as _mark  # noqa: PLC0415
    return _mark(interview_id)


def cancel_interview(interview_id: str):
    from cv_sender.interviews import cancel_interview as _cancel  # noqa: PLC0415
    return _cancel(interview_id)


def reschedule_interview(interview_id: str, new_datetime, update_calendar: bool = False):
    from cv_sender.interviews import reschedule_interview as _reschedule  # noqa: PLC0415
    return _reschedule(interview_id, new_datetime, update_calendar)


def get_interview_for_application(app_id: str):
    from cv_sender.interviews import get_interview_for_application as _get  # noqa: PLC0415
    return _get(app_id)
