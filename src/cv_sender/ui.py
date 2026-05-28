"""Streamlit UI for cv-sender – local job application assistant."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import streamlit as st

# ---------------------------------------------------------------------------
# Page config must be the very first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="cv-sender",
    page_icon="📋",
    layout="wide",
)

from cv_sender.config import (  # noqa: E402  (after set_page_config)
    AnswerGenerationConfig,
    AnswerProfileConfig,
    CalendarConfig,
    FollowUpConfig,
    GmailConfig,
    LMStudioConfig,
    Profile,
    Settings,
    load_profile,
    load_settings,
    save_profile,
    save_settings,
)
from cv_sender.models import (  # noqa: E402
    Application,
    ApplicationStatus,
    Decision,
    FillStatus,
    Interview,
    InterviewStatus,
    InterviewType,
    Offer,
)
from cv_sender.storage import (  # noqa: E402
    load_applications,
    load_offers,
    update_offer,
)
from cv_sender import services  # noqa: E402

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

_PAGES = ["Dashboard", "Offers", "Applications", "Profile", "Settings", "Gmail", "Interviews", "Analytics", "Job Search", "Rapid Apply", "Campaigns", "Bookmarklet", "Debug", "Data Cleanup"]

st.sidebar.title("cv-sender")
page = st.sidebar.radio("Navigate", _PAGES, label_visibility="collapsed")
st.sidebar.markdown("---")
st.sidebar.caption("⚠️ Forms are filled but **never** auto-submitted.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_load_offers() -> list[Offer]:
    try:
        return load_offers()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not load offers: {exc}")
        return []


def _safe_load_applications() -> list[Application]:
    try:
        return load_applications()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not load applications: {exc}")
        return []


def _salary_str(o: Offer | Application) -> str:
    if o.salary_min is not None:
        return f"{o.salary_min:,.0f}–{o.salary_max:,.0f} {o.currency}" if o.salary_max else f"{o.salary_min:,.0f} {o.currency}"
    return "—"


def _render_fill_result(result: Any, offer_id: str) -> None:
    """Render a FillResult in the UI including debug artifacts and retry buttons."""
    if result.status == FillStatus.FILLED:
        st.success("Application form filled successfully.")
    elif result.status == FillStatus.PARTIAL:
        st.warning("Form partially filled – some fields may need manual input.")
    else:
        st.error(result.error or "Form filling failed.")
    if result.fields_filled:
        st.info("Filled: " + ", ".join(result.fields_filled))
    if result.fields_missing:
        st.warning("Not filled: " + ", ".join(result.fields_missing))
    for w in result.warnings:
        st.warning(w)
    if result.status in (FillStatus.FILLED, FillStatus.PARTIAL):
        st.info(
            "Application form has been filled. "
            "Please review it manually before submitting."
        )
    if result.debug_run_id:
        with st.expander("Form filling debug", expanded=result.status != FillStatus.FILLED):
            st.caption(f"Debug run ID: `{result.debug_run_id}`")
            _render_debug_artifacts(result.debug_run_id, result.screenshot_path)

    generated = getattr(result, "generated_answers", [])
    if generated:
        with st.expander(f"Generated answers ({len(generated)})", expanded=False):
            import pandas as pd

            rows = [
                {
                    "Question": s.get("question", "")[:60],
                    "Type": s.get("question_type", ""),
                    "Source": s.get("source", ""),
                    "Confidence": f"{s.get('confidence', 0):.0%}",
                    "Filled": "✅" if s.get("filled") else "—",
                    "Preview": s.get("answer_preview", "")[:80],
                    "Warnings": "; ".join(s.get("warnings", [])),
                }
                for s in generated
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    rc1, rc2 = st.columns(2)
    if rc1.button("Retry with same filler", key=f"retry_same_{offer_id}"):
        with st.spinner("Retrying…"):
            retry_result = services.fill_application_form_retry(offer_id)
        _render_fill_result(retry_result, offer_id + "_r")
    if rc2.button("Retry with GenericFiller", key=f"retry_generic_{offer_id}"):
        with st.spinner("Retrying with generic filler…"):
            retry_result = services.fill_application_form_retry(offer_id, force_generic=True)
        _render_fill_result(retry_result, offer_id + "_rg")


def _render_debug_artifacts(run_id: str, screenshot_path: str = "") -> None:
    """Show step log, detected fields table, and screenshot for a debug run."""
    step_log = services.get_debug_step_log(run_id)
    if step_log:
        import pandas as pd

        df = pd.DataFrame(step_log)[["timestamp", "action", "target", "status", "message"]]
        st.dataframe(df, use_container_width=True)
    else:
        st.caption("No step log available.")

    snapshot = services.get_debug_form_snapshot(run_id)
    if snapshot:
        with st.expander(f"Detected form fields ({len(snapshot)})"):
            import pandas as pd

            st.dataframe(pd.DataFrame(snapshot), use_container_width=True)

    if screenshot_path:
        from pathlib import Path as _Path

        p = _Path(screenshot_path)
        if p.exists():
            with st.expander("Screenshot"):
                st.image(str(p), use_container_width=True)


def _rescore_offer(offer: Offer, settings: Settings) -> Offer:
    """Re-score *offer* via the service layer (uses LLM when enabled)."""
    ok, msg, updated = services.score_offer_by_id(
        offer.id, use_llm=settings.lm_studio.enabled
    )
    if not ok or updated is None:
        st.error(f"Scoring failed: {msg}")
        return offer
    if "⚠" in msg:
        st.warning(msg.split("|")[-1].strip())
    return updated


# ---------------------------------------------------------------------------
# Dashboard page
# ---------------------------------------------------------------------------


def _page_dashboard() -> None:
    st.title("Dashboard")

    offers = _safe_load_offers()
    apps = _safe_load_applications()
    settings = load_settings()

    total_offers = len(offers)
    apply_count = sum(1 for o in offers if o.decision == Decision.APPLY)
    skipped_count = sum(1 for o in offers if o.decision == Decision.SKIP)

    total_apps = len(apps)
    sent = sum(1 for a in apps if a.status == ApplicationStatus.SENT)
    follow_up_due_count = len(services.get_follow_up_due_applications())
    replies = sum(1 for a in apps if a.status == ApplicationStatus.REPLY_RECEIVED)
    interviews = sum(1 for a in apps if a.status == ApplicationStatus.INTERVIEW)
    rejected = sum(1 for a in apps if a.status == ApplicationStatus.REJECTED)
    got_offer = sum(1 for a in apps if a.status == ApplicationStatus.OFFER)
    stale_count = len(services.get_stale_applications())

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total offers", total_offers)
    col2.metric("Apply", apply_count)
    col3.metric("Skip", skipped_count)
    col4.metric("Applications sent", sent)

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("Follow-ups due", follow_up_due_count, delta=None if not follow_up_due_count else "!")
    col6.metric("Replies received", replies)
    col7.metric("Interviews", interviews)
    col8.metric("Offers received", got_offer)

    col9, col10 = st.columns([1, 3])
    col9.metric("No response / stale", stale_count)

    # Due now panel
    if settings.follow_up.enabled:
        due_apps = services.get_follow_up_due_applications()
        if due_apps:
            st.markdown("---")
            st.subheader(f"⏰ Follow-ups due now ({len(due_apps)})")
            now = datetime.now(UTC)
            for app in sorted(due_apps, key=lambda a: a.follow_up_due_at or now):
                days_since = (now - app.sent_at).days if app.sent_at else "?"
                due_str = app.follow_up_due_at.strftime("%Y-%m-%d") if app.follow_up_due_at else "—"
                with st.expander(f"**{app.company}** — {app.title}  |  due {due_str}", expanded=True):
                    dc1, dc2, dc3 = st.columns(3)
                    dc1.caption(f"Sent: {app.sent_at.strftime('%Y-%m-%d') if app.sent_at else '—'}")
                    dc2.caption(f"Days since sent: {days_since}")
                    dc3.caption(f"Due: {due_str}")
                    bc1, bc2, bc3, bc4 = st.columns(4)
                    if bc1.button("Mark follow-up sent", key=f"dash_fu_{app.id}"):
                        ok, msg = services.mark_follow_up_sent(app.id)
                        st.success(msg) if ok else st.error(msg)
                        st.rerun()
                    if bc2.button("Snooze 2 days", key=f"dash_snooze_{app.id}"):
                        ok, msg = services.snooze_application_reminder(app.id, 2)
                        st.success(msg) if ok else st.error(msg)
                        st.rerun()
                    if bc3.button("Mark reply received", key=f"dash_reply_{app.id}"):
                        ok, msg = services.mark_reply_received(app.id)
                        st.success(msg) if ok else st.error(msg)
                        st.rerun()
                    if bc4.button("Archive", key=f"dash_archive_{app.id}"):
                        ok, msg = services.archive_application(app.id)
                        st.success(msg) if ok else st.error(msg)
                        st.rerun()

    st.markdown("---")

    # Upcoming interviews panel
    upcoming = services.list_upcoming_interviews()
    if upcoming:
        st.subheader(f"📅 Upcoming interviews ({len(upcoming)})")
        for iv in sorted(upcoming, key=lambda i: i.interview_at):
            iv_dt = iv.interview_at.strftime("%Y-%m-%d %H:%M")
            with st.expander(f"**{iv.company}** — {iv.title}  |  {iv_dt}", expanded=False):
                ic1, ic2, ic3 = st.columns(3)
                ic1.caption(f"Type: {iv.interview_type}")
                ic2.caption(f"Duration: {iv.duration_minutes} min")
                ic3.caption(f"Source: {iv.source}")
                if iv.meeting_url:
                    st.caption(f"Meeting URL: {iv.meeting_url}")
                if iv.location:
                    st.caption(f"Location: {iv.location}")
                if iv.notes:
                    st.caption(f"Notes: {iv.notes}")
                bc1, bc2 = st.columns(2)
                if bc1.button("Mark completed", key=f"dash_iv_done_{iv.id}"):
                    ok, msg = services.mark_interview_completed(iv.id)
                    st.success(msg) if ok else st.error(msg)
                    st.rerun()
                if bc2.button("Cancel", key=f"dash_iv_cancel_{iv.id}"):
                    ok, msg = services.cancel_interview(iv.id)
                    st.success(msg) if ok else st.error(msg)
                    st.rerun()

    st.markdown("---")
    st.subheader("Recent offers")
    if offers:
        import pandas as pd

        recent = sorted(offers, key=lambda o: o.created_at, reverse=True)[:10]
        df = pd.DataFrame(
            [
                {
                    "Title": o.title,
                    "Company": o.company,
                    "Score": o.score,
                    "Decision": str(o.decision or "—"),
                    "Source": o.source,
                    "Added": o.created_at.date(),
                }
                for o in recent
            ]
        )
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No offers yet. Add one with `cv-sender add-offer` or import via the CLI.")


# ---------------------------------------------------------------------------
# Offers page
# ---------------------------------------------------------------------------


def _page_offers() -> None:
    st.title("Offers")

    settings = load_settings()

    # --- Add offer manually ---
    with st.expander("➕ Add offer manually", expanded=False):
        with st.form("add_offer_form", clear_on_submit=True):
            fc1, fc2 = st.columns(2)
            new_title = fc1.text_input("Job title *", placeholder="Senior Frontend Developer")
            new_company = fc2.text_input("Company", placeholder="ACME Corp")
            new_url = st.text_input("Offer URL *", placeholder="https://example.com/job/123")
            oc1, oc2, oc3 = st.columns(3)
            new_source = oc1.text_input("Source", value="manual")
            new_location = oc2.text_input("Location", placeholder="Warszawa / remote")
            new_contract = oc3.text_input("Contract", placeholder="B2B / UoP")
            sc1, sc2, sc3 = st.columns(3)
            new_sal_min = sc1.number_input("Salary min", min_value=0, value=0, step=500)
            new_sal_max = sc2.number_input("Salary max", min_value=0, value=0, step=500)
            new_currency = sc3.text_input("Currency", value="PLN")
            new_tech_raw = st.text_input("Technologies (comma-separated)", placeholder="React, TypeScript")
            new_description = st.text_area("Description", height=120)
            add_submitted = st.form_submit_button("Save offer")

        if add_submitted:
            if not new_title.strip():
                st.error("Job title is required.")
            elif not new_url.strip():
                st.error("Offer URL is required.")
            else:
                tech_list = [t.strip() for t in new_tech_raw.split(",") if t.strip()]
                saved, offer_obj = services.add_offer_manual(
                    url=new_url.strip(),
                    title=new_title.strip(),
                    company=new_company.strip(),
                    source=new_source.strip() or "manual",
                    location=new_location.strip(),
                    contract=new_contract.strip(),
                    salary_min=float(new_sal_min) if new_sal_min else None,
                    salary_max=float(new_sal_max) if new_sal_max else None,
                    currency=new_currency.strip() or "PLN",
                    technologies=tech_list,
                    description=new_description.strip(),
                )
                if saved:
                    st.success(f"Offer saved (id: {offer_obj.id[:8]}). Reloading…")
                    st.rerun()
                else:
                    st.warning("An offer with this URL already exists – skipped.")

    # --- Batch import URLs ---
    with st.expander("📋 Batch import URLs", expanded=False):
        with st.form("batch_import_form", clear_on_submit=True):
            raw_urls = st.text_area(
                "Job offer URLs (one per line)",
                height=160,
                placeholder="https://rocketjobs.pl/oferty/...\nhttps://pracuj.pl/praca/...",
            )
            bi_c1, bi_c2, bi_c3 = st.columns(3)
            batch_source = bi_c1.text_input(
                "Source override (optional)",
                placeholder="leave blank to auto-detect",
            )
            batch_auto_score = bi_c2.checkbox("Auto-score after import", value=True)
            batch_max = bi_c3.number_input(
                "Max URLs per batch",
                min_value=1,
                max_value=50,
                value=20,
                step=1,
            )
            batch_submitted = st.form_submit_button("Import URLs")

        if batch_submitted:
            from cv_sender.url_utils import parse_url_lines  # noqa: PLC0415

            url_list = parse_url_lines(raw_urls or "")
            if not url_list:
                st.warning("No URLs found. Paste at least one URL.")
            else:
                with st.status(f"Importing {len(url_list)} URL(s)…", expanded=True) as status_widget:
                    batch_result = services.import_offers_from_urls(
                        urls=url_list,
                        source_override=batch_source.strip() or None,
                        auto_score=batch_auto_score,
                        max_urls=int(batch_max),
                    )
                    status_widget.update(label="Import complete.", state="complete")

                # Summary metrics
                m1, m2, m3, m4, m5, m6 = st.columns(6)
                m1.metric("Imported", batch_result.imported_count)
                m2.metric("Duplicates", batch_result.duplicate_count)
                m3.metric("Failed", batch_result.failed_count)
                m4.metric("Invalid", batch_result.invalid_count)
                m5.metric("Skipped (limit)", batch_result.skipped_limit_count)
                m6.metric("Scored", batch_result.scored_count)

                # Per-URL table
                import pandas as pd  # noqa: PLC0415

                rows = [
                    {
                        "URL": item.url[:80] + ("…" if len(item.url) > 80 else ""),
                        "Status": item.status,
                        "ID": (item.offer_id or "")[:8],
                        "Title": item.title[:40] + ("…" if len(item.title) > 40 else "") if item.title else "",
                        "Score": item.score if item.score is not None else "",
                        "Decision": str(item.decision or ""),
                        "Error": item.error[:60] + ("…" if len(item.error) > 60 else "") if item.error else "",
                    }
                    for item in batch_result.items
                ]
                if rows:
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

                if batch_result.imported_count:
                    st.rerun()

    offers = _safe_load_offers()

    if not offers:
        st.info("No offers yet. Use the forms above to add one.")
        return

    from cv_sender.listing import (  # noqa: PLC0415
        ListQuery,
        build_list_result,
        init_list_state,
        render_pagination_controls,
        render_search_box,
    )

    _PFX = "offers"
    init_list_state(_PFX, {"page_size": 25, "sort_by": "created_at", "sort_dir": "desc"})

    # --- Search + Filters + Sort controls ---
    with st.expander("🔍 Search, Filter & Sort", expanded=True):
        sc1, sc2 = st.columns([3, 1])
        with sc1:
            search_text = render_search_box(_PFX, label="Search offers", placeholder="title, company, location…")
        with sc2:
            page_size: int = st.selectbox(
                "Per page",
                [10, 25, 50, 100],
                index=[10, 25, 50, 100].index(st.session_state.get(f"{_PFX}_page_size", 25)),
                key=f"{_PFX}_page_size",
            )

        fc1, fc2, fc3, fc4 = st.columns(4)
        decision_opts = ["(all)"] + [d.value for d in Decision]
        sel_decision: str = fc1.selectbox(
            "Decision", decision_opts, key=f"{_PFX}_filter_decision"
        )
        sources = sorted({o.source for o in offers if o.source})
        sel_source: str = fc2.selectbox(
            "Source", ["(all)"] + sources, key=f"{_PFX}_filter_source"
        )
        locations = sorted({o.location for o in offers if o.location})
        sel_location: str = fc3.selectbox(
            "Location", ["(all)"] + locations, key=f"{_PFX}_filter_location"
        )
        sel_only_not_applied: bool = fc4.checkbox(
            "Not yet applied", key=f"{_PFX}_filter_not_applied"
        )

        sf1, sf2, sf3 = st.columns(3)
        sel_min_score: int = sf1.number_input(
            "Min score", min_value=0, max_value=100, value=0, step=5, key=f"{_PFX}_filter_min_score"
        )
        sel_max_score: int = sf2.number_input(
            "Max score", min_value=0, max_value=100, value=100, step=5, key=f"{_PFX}_filter_max_score"
        )
        _sort_opts = ["created_at", "score", "priority_score", "company", "title", "source"]
        sort_by: str = sf3.selectbox(
            "Sort by", _sort_opts,
            index=_sort_opts.index(st.session_state.get(f"{_PFX}_sort_by") or "created_at")
            if (st.session_state.get(f"{_PFX}_sort_by") or "created_at") in _sort_opts else 0,
            key=f"{_PFX}_sort_by",
        )
        sort_dir: str = sf1.selectbox(
            "Direction", ["Descending", "Ascending"], key=f"{_PFX}_sort_dir_label"
        )
        _sort_dir = "asc" if sort_dir == "Ascending" else "desc"
        st.session_state[f"{_PFX}_sort_dir"] = _sort_dir

    # Reset to page 1 when any filter changes
    _watched = (
        search_text,
        sel_decision,
        sel_source,
        sel_location,
        sel_only_not_applied,
        sel_min_score,
        sel_max_score,
        sort_by,
        _sort_dir,
        page_size,
    )
    _sentinel_key = f"{_PFX}_filter_sentinel"
    if st.session_state.get(_sentinel_key) != _watched:
        st.session_state[f"{_PFX}_page"] = 1
    st.session_state[_sentinel_key] = _watched

    def _offer_filter_fn(o) -> bool:  # noqa: ANN001
        if sel_decision != "(all)" and str(o.decision or "") != sel_decision:
            return False
        if sel_source != "(all)" and (o.source or "") != sel_source:
            return False
        if sel_location != "(all)" and (o.location or "") != sel_location:
            return False
        if sel_min_score > 0 and (o.score or 0) < sel_min_score:
            return False
        if sel_max_score < 100 and (o.score or 0) > sel_max_score:
            return False
        if sel_only_not_applied and str(o.decision or "") in ("skip", "maybe"):
            return False
        return True

    query = ListQuery(
        page=st.session_state.get(f"{_PFX}_page", 1),
        page_size=page_size,
        search_text=search_text,
        sort_by=sort_by,
        sort_dir=_sort_dir,
    )
    result = build_list_result(
        offers,
        query,
        search_fields=["title", "company", "location", "source", "technologies"],
        filter_fn=_offer_filter_fn,
    )

    # Bulk select / delete on current page
    with st.expander("🗑️ Bulk actions (current page)", expanded=False):
        st.caption(
            f"Select offers on the current page ({len(result.items)}) to delete them. "
            "Use **'Select all matching'** to target all filtered results."
        )
        _sel_all_matching = st.checkbox(
            f"Select all {result.total_count} matching offers",
            key=f"{_PFX}_sel_all_matching",
        )
        if _sel_all_matching:
            st.warning(
                f"⚠️ This will delete **all {result.total_count} filtered offers**. "
                "Confirm with the button below."
            )
            if st.button(
                f"🗑️ Delete all {result.total_count} matching offers",
                type="primary",
                key=f"{_PFX}_del_all_matching",
            ):
                _all_filtered = build_list_result(
                    offers,
                    ListQuery(page=1, page_size=len(offers), sort_by=sort_by, sort_dir=_sort_dir),
                    search_fields=["title", "company", "location", "source", "technologies"],
                    filter_fn=_offer_filter_fn,
                )
                _deleted = 0
                for _o in _all_filtered.items:
                    from cv_sender.storage import delete_offer  # noqa: PLC0415
                    try:
                        delete_offer(_o.id)
                        _deleted += 1
                    except Exception:  # noqa: BLE001
                        pass
                st.success(f"Deleted {_deleted} offers.")
                st.session_state[f"{_PFX}_sel_all_matching"] = False
                st.rerun()

    # Pagination header
    new_page = render_pagination_controls(_PFX, result)
    if new_page != st.session_state.get(f"{_PFX}_page"):
        st.session_state[f"{_PFX}_page"] = new_page
        st.rerun()

    for offer in result.items:
        with st.expander(f"**{offer.title}** — {offer.company}  |  score: {offer.score or '—'}  |  {offer.decision or '—'}"):
            c1, c2, c3 = st.columns(3)
            c1.markdown(f"**Source:** {offer.source or '—'}")
            c1.markdown(f"**Location:** {offer.location or '—'}")
            c1.markdown(f"**Contract:** {offer.contract or '—'}")
            c2.markdown(f"**Salary:** {_salary_str(offer)}")
            c2.markdown(f"**Technologies:** {', '.join(offer.technologies) or '—'}")
            c3.markdown(f"**Score:** {offer.score}")
            c3.markdown(f"**Decision:** {offer.decision or '—'}")
            if offer.url:
                st.markdown(f"[Open offer URL]({offer.url})")

            if offer.decision_reasons:
                st.markdown("**Reasons:** " + " · ".join(offer.decision_reasons))
            if offer.risks:
                st.markdown("**Risks:** " + " · ".join(offer.risks))

            if offer.extraction_source:
                with st.expander("Extraction details", expanded=False):
                    ec1, ec2 = st.columns(2)
                    ec1.markdown(f"**Source:** `{offer.extraction_source}`")
                    confidence_pct = f"{offer.extraction_confidence:.0%}" if offer.extraction_confidence else "—"
                    ec2.markdown(f"**Confidence:** {confidence_pct}")
                    if offer.extraction_warnings:
                        for w in offer.extraction_warnings:
                            st.warning(w)

            btn_col1, btn_col2, btn_col3, btn_col4 = st.columns(4)

            if btn_col1.button("Re-score", key=f"rescore_{offer.id}"):
                with st.spinner("Scoring…"):
                    updated = _rescore_offer(offer, settings)
                if updated is not offer:
                    st.success(f"Re-scored: {updated.score} / {updated.decision}")
                    st.rerun()

            if btn_col2.button("Mark skipped", key=f"skip_{offer.id}"):
                updated = offer.model_copy(update={"decision": Decision.SKIP})
                update_offer(updated)
                st.rerun()

            if btn_col3.button("Mark ready to send", key=f"ready_{offer.id}"):
                updated = offer.model_copy(update={"decision": Decision.APPLY})
                update_offer(updated)
                st.rerun()

            if btn_col4.button("Fill application form", key=f"fill_{offer.id}"):
                # Determine selected CV from session state dropdown
                _cv_key = f"cv_sel_{offer.id}"
                _chosen_cv_id = st.session_state.get(_cv_key, "")
                with st.spinner("Opening browser and filling form…"):
                    result = services.fill_application_form(offer.id, selected_cv_id=_chosen_cv_id)
                _render_fill_result(result, offer.id)

            # CV selection expander (shown below buttons)
            _cv_profiles = services.list_cv_profiles()
            if _cv_profiles:
                with st.expander("CV selection", expanded=False):
                    _cv_rec = services.select_cv_for_offer_object_svc(offer)
                    if _cv_rec.selected_cv_id:
                        st.info(
                            f"Auto-recommended: **{_cv_rec.selected_cv_name or _cv_rec.selected_cv_id}**"
                            + (f"  (score {_cv_rec.score})" if _cv_rec.score else "")
                        )
                        if _cv_rec.reasons:
                            st.caption("Reasons: " + ", ".join(_cv_rec.reasons[:3]))
                    for w in _cv_rec.warnings:
                        st.warning(w)
                    _cv_options = {cv.id: (cv.name or cv.id) for cv in _cv_profiles}
                    _default_idx = (
                        list(_cv_options.keys()).index(_cv_rec.selected_cv_id)
                        if _cv_rec.selected_cv_id in _cv_options
                        else 0
                    )
                    _selected = st.selectbox(
                        "Override CV",
                        options=list(_cv_options.keys()),
                        format_func=lambda k: _cv_options[k],
                        index=_default_idx,
                        key=f"cv_sel_{offer.id}",
                    )
                    # Show whether the selected file exists
                    _sel_cv_obj = next((cv for cv in _cv_profiles if cv.id == _selected), None)
                    if _sel_cv_obj:
                        _path_ok = Path(_sel_cv_obj.path).exists() if _sel_cv_obj.path else False
                        st.caption(
                            f"Path: `{_sel_cv_obj.path}`  {'✅' if _path_ok else '⚠️ file not found'}"
                        )

            if st.button("Preview application answers", key=f"preview_answers_{offer.id}"):
                with st.spinner("Generating preview answers…"):
                    _preview_answers = services.preview_application_answers(offer.id)
                if _preview_answers:
                    for _ans in _preview_answers:
                        _badge = "🟢" if _ans.confidence >= 0.65 else ("🟡" if _ans.confidence >= 0.4 else "🔴")
                        st.markdown(f"**{_ans.question}** `{_ans.question_type}` {_badge}")
                        if _ans.answer:
                            st.text_area(
                                "Answer",
                                value=_ans.answer,
                                height=80,
                                key=f"prev_{offer.id}_{_ans.question[:20]}",
                                disabled=True,
                            )
                        else:
                            st.warning("No answer generated")
                        for _w in _ans.warnings:
                            st.warning(_w)
                else:
                    st.info("No answers generated.")


# ---------------------------------------------------------------------------
# Applications page
# ---------------------------------------------------------------------------


def _page_applications() -> None:
    st.title("Applications")

    all_apps = _safe_load_applications()

    if not all_apps:
        st.info("No applications yet. The file `data/applications.json` is empty or does not exist.")
        return

    import pandas as pd

    from cv_sender.follow_up import is_follow_up_due  # noqa: PLC0415
    from cv_sender.listing import (  # noqa: PLC0415
        ListQuery,
        build_list_result,
        init_list_state,
        render_pagination_controls,
    )

    _PFX = "applications"
    init_list_state(_PFX, {"page_size": 25, "sort_by": "updated_at", "sort_dir": "desc"})

    now = datetime.now(UTC)
    status_values = [s.value for s in ApplicationStatus]

    # ── Filters ──────────────────────────────────────────────────────────────
    with st.expander("🔍 Search, Filter & Sort", expanded=False):
        sc1, sc2 = st.columns([3, 1])
        with sc1:
            app_search: str = st.text_input(
                "Search", placeholder="title, company, source…", key=f"{_PFX}_search"
            )
        with sc2:
            app_page_size: int = st.selectbox(
                "Per page",
                [10, 25, 50, 100],
                index=[10, 25, 50, 100].index(st.session_state.get(f"{_PFX}_page_size", 25)),
                key=f"{_PFX}_page_size",
            )

        fc1, fc2, fc3 = st.columns(3)
        filter_status: str = fc1.selectbox(
            "Status", ["(all)"] + status_values, key=f"{_PFX}_filter_status"
        )
        app_sources = sorted({a.source for a in all_apps if a.source})
        filter_source: str = fc2.selectbox(
            "Source", ["(all)"] + app_sources, key=f"{_PFX}_filter_source"
        )
        filter_company: str = fc3.text_input("Company contains", key=f"{_PFX}_filter_company")

        fc4, fc5 = st.columns(2)
        filter_due_only: bool = fc4.checkbox(
            "Due follow-ups only", key=f"{_PFX}_filter_due"
        )

        _sort_opts_app = ["updated_at", "sent_at", "follow_up_due_at", "company", "status"]
        filter_sort_by: str = fc5.selectbox(
            "Sort by", _sort_opts_app,
            index=_sort_opts_app.index(st.session_state.get(f"{_PFX}_sort_by") or "updated_at")
            if (st.session_state.get(f"{_PFX}_sort_by") or "updated_at") in _sort_opts_app else 0,
            key=f"{_PFX}_sort_by",
        )

    # Reset page when filters change
    _watched_app = (app_search, filter_status, filter_source, filter_company, filter_due_only, filter_sort_by, app_page_size)
    _sentinel_app = f"{_PFX}_filter_sentinel"
    if st.session_state.get(_sentinel_app) != _watched_app:
        st.session_state[f"{_PFX}_page"] = 1
    st.session_state[_sentinel_app] = _watched_app

    def _app_filter_fn(a: Application) -> bool:
        if filter_status != "(all)" and a.status.value != filter_status:
            return False
        if filter_source != "(all)" and (a.source or "") != filter_source:
            return False
        if filter_due_only and not is_follow_up_due(a, now):
            return False
        if filter_company and filter_company.lower() not in (a.company or "").lower():
            return False
        return True

    query = ListQuery(
        page=st.session_state.get(f"{_PFX}_page", 1),
        page_size=app_page_size,
        search_text=app_search or None,
        sort_by=filter_sort_by,
        sort_dir="desc",
    )
    result = build_list_result(
        all_apps,
        query,
        search_fields=["title", "company", "source", "location"],
        filter_fn=_app_filter_fn,
    )

    new_page = render_pagination_controls(_PFX, result)
    if new_page != st.session_state.get(f"{_PFX}_page"):
        st.session_state[f"{_PFX}_page"] = new_page
        st.rerun()

    for app in result.items:
        due_badge = " ⏰" if is_follow_up_due(app, now) else ""
        label = f"**{app.title}** — {app.company}  |  {app.status}{due_badge}  |  {app.created_at.date()}"
        with st.expander(label):
            c1, c2, c3 = st.columns(3)
            c1.markdown(f"**Source:** {app.source or '—'}")
            c1.markdown(f"**Location:** {app.location or '—'}")
            c1.markdown(f"**Contract:** {app.contract or '—'}")
            c2.markdown(f"**Salary:** {_salary_str(app)}")
            c2.markdown(f"**Score:** {app.score or '—'}")
            c3.markdown(f"**Created:** {app.created_at.date()}")
            c3.markdown(f"**Updated:** {app.updated_at.date()}")
            if app.selected_cv_name or app.selected_cv_id:
                c3.markdown(f"**CV used:** {app.selected_cv_name or app.selected_cv_id}")
            if app.url:
                st.markdown(f"[Offer URL]({app.url})")

            # Follow-up tracking info
            if app.sent_at or app.follow_up_due_at or app.next_action_at:
                fi1, fi2, fi3, fi4 = st.columns(4)
                fi1.caption(f"Sent: {app.sent_at.strftime('%Y-%m-%d') if app.sent_at else '—'}")
                fi2.caption(f"Follow-up due: {app.follow_up_due_at.strftime('%Y-%m-%d') if app.follow_up_due_at else '—'}")
                fi3.caption(f"Next action: {app.next_action_at.strftime('%Y-%m-%d') if app.next_action_at else '—'} ({app.next_action_type or '—'})")
                fi4.caption(f"Last contact: {app.last_contact_at.strftime('%Y-%m-%d') if app.last_contact_at else '—'}")

            # ── Status/notes form ─────────────────────────────────────────
            with st.form(key=f"app_form_{app.id}"):
                new_status = st.selectbox(
                    "Status",
                    status_values,
                    index=status_values.index(app.status.value),
                    key=f"status_sel_{app.id}",
                )
                new_notes = st.text_area("Notes", value=app.notes or "", key=f"notes_{app.id}")
                save_btn = st.form_submit_button("Save changes")

            if save_btn:
                if new_status != app.status.value:
                    ok, msg = services.update_application_status(
                        app.id, ApplicationStatus(new_status)
                    )
                    if not ok:
                        st.error(msg)
                if new_notes != (app.notes or ""):
                    ok, msg = services.update_application_notes(app.id, new_notes)
                    if not ok:
                        st.error(msg)
                st.success("Saved.")
                st.rerun()

            # ── Quick action buttons ──────────────────────────────────────
            st.markdown("**Quick actions:**")
            qa1, qa2, qa3, qa4 = st.columns(4)
            qa5, qa6, qa7, qa8 = st.columns(4)

            if qa1.button("Mark as sent", key=f"qa_sent_{app.id}"):
                ok, msg = services.mark_application_sent(app.id)
                st.success(msg) if ok else st.error(msg)
                st.rerun()

            if qa2.button("Follow-up sent", key=f"qa_fu_{app.id}"):
                ok, msg = services.mark_follow_up_sent(app.id)
                st.success(msg) if ok else st.error(msg)
                st.rerun()

            if qa3.button("Reply received", key=f"qa_reply_{app.id}"):
                ok, msg = services.mark_reply_received(app.id)
                st.success(msg) if ok else st.error(msg)
                st.rerun()

            if qa4.button("Snooze 2 days", key=f"qa_snooze_{app.id}"):
                ok, msg = services.snooze_application_reminder(app.id, 2)
                st.success(msg) if ok else st.error(msg)
                st.rerun()

            if qa5.button("Mark rejected", key=f"qa_rej_{app.id}"):
                ok, msg = services.update_application_status(app.id, ApplicationStatus.REJECTED)
                st.success(msg) if ok else st.error(msg)
                st.rerun()

            if qa6.button("Mark offer", key=f"qa_offer_{app.id}"):
                ok, msg = services.update_application_status(app.id, ApplicationStatus.OFFER)
                st.success(msg) if ok else st.error(msg)
                st.rerun()

            if qa7.button("Archive", key=f"qa_archive_{app.id}"):
                ok, msg = services.archive_application(app.id)
                st.success(msg) if ok else st.error(msg)
                st.rerun()

            # ── Schedule interview ────────────────────────────────────────
            with st.expander("📅 Schedule interview", expanded=False):
                with st.form(key=f"interview_form_{app.id}"):
                    iv_date = st.date_input("Interview date", key=f"iv_date_{app.id}")
                    iv_time = st.time_input("Interview time", key=f"iv_time_{app.id}")
                    iv_note = st.text_input("Note (optional)", key=f"iv_note_{app.id}")
                    iv_btn = st.form_submit_button("Schedule")
                if iv_btn:
                    from datetime import timezone as _tz  # noqa: PLC0415
                    iv_dt = datetime.combine(iv_date, iv_time, tzinfo=UTC)
                    ok, msg = services.schedule_interview(app.id, iv_dt, note=iv_note or None)
                    st.success(msg) if ok else st.error(msg)
                    st.rerun()

            # ── Follow-up message draft ───────────────────────────────────
            with st.expander("✉️ Follow-up message draft", expanded=False):
                msg_text = services.generate_follow_up_message_for(app.id)
                if msg_text:
                    st.text_area(
                        "Copy and send manually",
                        value=msg_text,
                        height=150,
                        key=f"fu_msg_{app.id}",
                    )
                else:
                    st.caption("Application not found.")

            # ── Matched emails ────────────────────────────────────────────
            email_matches = services.get_matches_for_application(app.id)
            if email_matches:
                with st.expander(f"📧 Matched emails ({len(email_matches)})"):
                    for em in email_matches:
                        st.markdown(
                            f"**{em.subject or '(no subject)'}**  —  "
                            f"`{em.from_email}`  |  {em.received_at.strftime('%Y-%m-%d')}"
                        )
                        st.caption(
                            f"Classification: `{em.classification}`  "
                            f"Confidence: {em.confidence:.0%}  "
                            f"Suggestion: `{em.status_suggestion}`  "
                            f"Status: {em.status}"
                        )
                        if em.snippet:
                            st.caption(em.snippet[:200])
                        if em.status == "pending":
                            ea1, ea2 = st.columns(2)
                            if ea1.button("Apply suggestion", key=f"em_apply_{em.id}"):
                                ok, msg = services.apply_email_match(em.id)
                                st.success(msg) if ok else st.error(msg)
                                st.rerun()
                            if ea2.button("Ignore", key=f"em_ignore_{em.id}"):
                                ok, msg = services.ignore_email_match(em.id)
                                st.success(msg) if ok else st.error(msg)
                                st.rerun()
                        st.divider()

            # ── Events timeline ───────────────────────────────────────────
            if app.events:
                with st.expander("📋 Events timeline"):
                    for ev in reversed(app.events):
                        st.caption(f"{ev.timestamp.strftime('%Y-%m-%d %H:%M')}  **{ev.event}**  {ev.details}")

            # ── Debug artifacts ───────────────────────────────────────────
            fill_events = [
                ev for ev in app.events
                if ev.event in ("form_filled", "fill_failed", "form_filled_retry", "fill_failed_retry")
            ]
            if fill_events:
                runs = services.get_debug_runs(limit=20)
                matching = [r for r in runs if r.offer_id == app.offer_id]
                if matching:
                    latest_run = matching[0]
                    with st.expander(f"Form filling debug (run {latest_run.run_id[:8]}…)"):
                        st.caption(f"Status: **{latest_run.status}** | Filler: {latest_run.filler_name} | {latest_run.started_at.strftime('%Y-%m-%d %H:%M')}")
                        if latest_run.warnings:
                            for w in latest_run.warnings:
                                st.warning(w)
                        if latest_run.error:
                            st.error(latest_run.error)
                        _render_debug_artifacts(latest_run.run_id, latest_run.screenshot_path)



# ---------------------------------------------------------------------------
# Profile page
# ---------------------------------------------------------------------------


def _page_profile() -> None:
    st.title("Profile")

    profile_path = Path("config/profile.yaml")
    if not profile_path.exists():
        st.warning(
            "Profile file not found (`config/profile.yaml`). "
            "Run `cv-sender init` to create it from the example template."
        )

    profile = load_profile()

    with st.form("profile_form"):
        st.subheader("Personal information")
        pc1, pc2 = st.columns(2)
        first_name = pc1.text_input("First name", value=profile.first_name)
        last_name = pc2.text_input("Last name", value=profile.last_name)
        email = pc1.text_input("Email", value=profile.email)
        phone = pc2.text_input("Phone", value=profile.phone)
        city = pc1.text_input("City", value=profile.city)

        st.subheader("Online profiles")
        lc1, lc2, lc3 = st.columns(3)
        linkedin = lc1.text_input("LinkedIn URL", value=profile.linkedin)
        github = lc2.text_input("GitHub URL", value=profile.github)
        portfolio = lc3.text_input("Portfolio URL", value=profile.portfolio)

        st.subheader("CV & availability")
        cv_path = st.text_input("CV file path", value=profile.cv_path)
        ac1, ac2, ac3 = st.columns(3)
        availability = ac1.text_input("Availability", value=profile.availability)
        notice_period = ac2.text_input("Notice period", value=profile.notice_period)
        english_level = ac3.text_input("English level", value=profile.english_level)
        preferred_work_mode = st.text_input("Preferred work mode", value=profile.preferred_work_mode)

        st.subheader("Salary expectations")
        sc1, sc2 = st.columns(2)
        expected_salary_b2b = sc1.number_input(
            "Expected salary B2B (gross/month)",
            min_value=0,
            value=profile.expected_salary_b2b or 0,
            step=500,
        )
        expected_salary_uop = sc2.number_input(
            "Expected salary UoP (gross/month)",
            min_value=0,
            value=profile.expected_salary_uop or 0,
            step=500,
        )

        submitted = st.form_submit_button("Save profile")

    if submitted:
        updated = Profile(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            city=city,
            linkedin=linkedin,
            github=github,
            portfolio=portfolio,
            cv_path=cv_path,
            expected_salary_b2b=expected_salary_b2b or None,
            expected_salary_uop=expected_salary_uop or None,
            availability=availability,
            notice_period=notice_period,
            english_level=english_level,
            preferred_work_mode=preferred_work_mode,
            consents=profile.consents,
            default_cv_id=profile.default_cv_id,
            cv_profiles=profile.cv_profiles,
        )
        save_profile(updated)
        st.success("Profile saved to `config/profile.yaml`.")

    # CV Profiles section (read-only, outside form)
    st.markdown("---")
    st.subheader("CV Profiles")
    _cv_profiles_all = services.list_cv_profiles()
    _cv_warnings = services.validate_cv_profiles()
    if _cv_warnings:
        for _w in _cv_warnings:
            st.warning(_w)
    if not _cv_profiles_all:
        st.info(
            "No CV profiles configured. Add `cv_profiles:` to `config/profile.yaml` "
            "to enable automatic CV selection."
        )
    else:
        import pandas as _pd  # noqa: PLC0415

        _rows = []
        for cv in _cv_profiles_all:
            _file_ok = Path(cv.path).exists() if cv.path else False
            _rows.append({
                "ID": cv.id,
                "Name": cv.name or cv.id,
                "Path": cv.path or "—",
                "File": "✅" if _file_ok else "⚠️",
                "Roles": ", ".join(cv.target_roles) or "—",
                "Tech": ", ".join(cv.technologies) or "—",
                "Seniority": ", ".join(cv.seniority) or "—",
                "Priority": cv.priority,
                "Active": "✅" if cv.active else "❌",
            })
        st.dataframe(_pd.DataFrame(_rows), use_container_width=True, hide_index=True)
        if profile.default_cv_id:
            st.caption(f"Default CV id: `{profile.default_cv_id}`")
        st.caption(
            "To add or modify CV profiles, edit `cv_profiles:` in `config/profile.yaml`."
        )


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------


def _page_settings() -> None:
    st.title("Settings")

    settings_path = Path("config/settings.yaml")
    if not settings_path.exists():
        st.warning(
            "Settings file not found (`config/settings.yaml`). "
            "Run `cv-sender init` to create it from the example template."
        )

    settings = load_settings()

    with st.form("settings_form"):
        st.subheader("Search criteria")
        role = st.text_input("Target role", value=settings.role)

        tech_raw = st.text_input(
            "Technologies (comma-separated)",
            value=", ".join(settings.technologies),
        )

        lc1, lc2 = st.columns(2)
        locations_raw = lc1.text_input(
            "Locations (comma-separated)",
            value=", ".join(settings.locations),
        )
        contract_raw = lc2.text_input(
            "Contract types (comma-separated)",
            value=", ".join(settings.contract_types),
        )

        st.subheader("Salary minimums")
        sc1, sc2 = st.columns(2)
        min_salary_b2b = sc1.number_input(
            "Min salary B2B",
            min_value=0,
            value=settings.min_salary_b2b or 0,
            step=500,
        )
        min_salary_uop = sc2.number_input(
            "Min salary UoP",
            min_value=0,
            value=settings.min_salary_uop or 0,
            step=500,
        )

        st.subheader("Scoring & automation")
        auto_apply_min_score = st.slider(
            "Auto-apply min score",
            min_value=0,
            max_value=100,
            value=settings.auto_apply_min_score,
        )
        require_manual_confirm = st.checkbox(
            "Require manual confirm before submitting",
            value=settings.require_manual_confirm,
        )
        skip_without_salary = st.checkbox(
            "Skip offers without salary info",
            value=settings.skip_without_salary,
        )

        st.subheader("LM Studio")
        lm_enabled = st.checkbox("LM Studio enabled", value=settings.lm_studio.enabled)
        lm_base_url = st.text_input("LM Studio base URL", value=settings.lm_studio.base_url)
        lm_model = st.text_input("LM Studio model name", value=settings.lm_studio.model)

        st.subheader("Answer generation")
        ans_enabled = st.checkbox("Enable answer generation", value=settings.answers.enabled)
        ans_use_llm = st.checkbox("Use LM Studio for complex questions", value=settings.answers.use_llm)
        ans_autofill = st.checkbox("Auto-fill generated answers", value=settings.answers.auto_fill_generated_answers)
        ans_min_conf = st.slider(
            "Min confidence to auto-fill",
            min_value=0.0, max_value=1.0,
            value=float(settings.answers.min_confidence_to_autofill),
            step=0.05,
        )
        ans_max_chars = st.number_input(
            "Max answer chars",
            min_value=100, max_value=2000,
            value=settings.answers.max_answer_chars,
            step=50,
        )

        st.subheader("Answer profile")
        ap_short_bio = st.text_area("Short bio", value=settings.answer_profile.short_bio, height=80)
        ap_years = st.text_input("Years of experience", value=settings.answer_profile.years_experience)
        ap_skills_raw = st.text_input(
            "Strongest skills (comma-separated)",
            value=", ".join(settings.answer_profile.strongest_skills),
        )
        ap_english = st.text_input("English level", value=settings.answer_profile.english_level)
        ap_salary_b2b = st.text_input("Salary expectation B2B", value=settings.answer_profile.salary_b2b)
        ap_salary_uop = st.text_input("Salary expectation UoP", value=settings.answer_profile.salary_uop)
        ap_motivation = st.text_area(
            "General motivation", value=settings.answer_profile.motivation_general, height=80
        )

        st.subheader("Follow-up tracking")
        fu_enabled = st.checkbox("Enable follow-up reminders", value=settings.follow_up.enabled)
        fu_days = st.number_input(
            "Default follow-up after (days)",
            min_value=1, max_value=30,
            value=settings.follow_up.default_follow_up_after_days,
        )
        fu_no_response = st.number_input(
            "Mark no-response after (days)",
            min_value=1, max_value=90,
            value=settings.follow_up.mark_no_response_after_days,
        )
        fu_show_within = st.number_input(
            "Show due within (days)",
            min_value=0, max_value=14,
            value=settings.follow_up.show_due_within_days,
        )
        fu_weekends = st.checkbox(
            "Allow weekend due dates",
            value=settings.follow_up.allow_weekend_due_dates,
        )

        st.subheader("Gmail (read-only)")
        gm_enabled = st.checkbox("Enable Gmail integration", value=settings.gmail.enabled)
        gm_creds = st.text_input("Credentials file path", value=settings.gmail.credentials_path)
        gm_token = st.text_input("Token file path", value=settings.gmail.token_path)
        gc1, gc2 = st.columns(2)
        gm_scan_days = gc1.number_input(
            "Scan days back", min_value=1, max_value=90, value=settings.gmail.scan_days_back
        )
        gm_max_results = gc2.number_input(
            "Max results", min_value=10, max_value=500, value=settings.gmail.max_results
        )
        gm_store_snippet = st.checkbox("Store email snippets", value=settings.gmail.store_snippet)
        gm_store_body = st.checkbox(
            "Store full email bodies (not recommended)", value=settings.gmail.store_email_body
        )
        gm_auto_update = st.checkbox(
            "Auto-apply status suggestions (not recommended)",
            value=settings.gmail.auto_update_status,
        )

        st.subheader("Google Calendar")
        cal_enabled = st.checkbox("Enable Calendar integration", value=settings.calendar.enabled)
        cal_creds = st.text_input(
            "Credentials file path (same as Gmail)", value=settings.calendar.credentials_path
        )
        cal_token = st.text_input("Calendar token file path", value=settings.calendar.token_path)
        cc1, cc2 = st.columns(2)
        cal_calendar_id = cc1.text_input("Calendar ID", value=settings.calendar.calendar_id)
        cal_tz = cc2.text_input("Timezone", value=settings.calendar.timezone)
        cc3, cc4 = st.columns(2)
        cal_duration = cc3.number_input(
            "Default interview duration (min)",
            min_value=15, max_value=480,
            value=settings.calendar.default_interview_duration_minutes,
        )
        cal_create_events = cc4.checkbox(
            "Allow creating calendar events", value=settings.calendar.create_calendar_events
        )
        cal_reminders = st.checkbox("Add reminders", value=settings.calendar.add_reminders)
        cal_reminder_raw = st.text_input(
            "Reminder minutes before (comma-separated)",
            value=", ".join(str(m) for m in settings.calendar.reminder_minutes_before),
        )

        submitted = st.form_submit_button("Save settings")

    if submitted:
        updated = Settings(
            role=role,
            technologies=[t.strip() for t in tech_raw.split(",") if t.strip()],
            min_salary_b2b=min_salary_b2b or None,
            min_salary_uop=min_salary_uop or None,
            locations=[loc.strip() for loc in locations_raw.split(",") if loc.strip()],
            contract_types=[c.strip() for c in contract_raw.split(",") if c.strip()],
            auto_apply_min_score=auto_apply_min_score,
            require_manual_confirm=require_manual_confirm,
            skip_without_salary=skip_without_salary,
            lm_studio=LMStudioConfig(
                enabled=lm_enabled,
                base_url=lm_base_url,
                api_key=settings.lm_studio.api_key,
                model=lm_model,
            ),
            answers=AnswerGenerationConfig(
                enabled=ans_enabled,
                use_llm=ans_use_llm,
                auto_fill_generated_answers=ans_autofill,
                min_confidence_to_autofill=ans_min_conf,
                max_answer_chars=int(ans_max_chars),
                require_review_for_low_confidence=settings.answers.require_review_for_low_confidence,
            ),
            answer_profile=AnswerProfileConfig(
                short_bio=ap_short_bio,
                years_experience=ap_years,
                strongest_skills=[s.strip() for s in ap_skills_raw.split(",") if s.strip()],
                industries=settings.answer_profile.industries,
                work_style=settings.answer_profile.work_style,
                motivation_general=ap_motivation,
                salary_b2b=ap_salary_b2b,
                salary_uop=ap_salary_uop,
                english_level=ap_english,
            ),
            answer_templates=settings.answer_templates,
            follow_up=FollowUpConfig(
                enabled=fu_enabled,
                default_follow_up_after_days=int(fu_days),
                mark_no_response_after_days=int(fu_no_response),
                show_due_within_days=int(fu_show_within),
                allow_weekend_due_dates=fu_weekends,
            ),
            gmail=GmailConfig(
                enabled=gm_enabled,
                credentials_path=gm_creds,
                token_path=gm_token,
                readonly=True,
                scan_days_back=int(gm_scan_days),
                max_results=int(gm_max_results),
                store_snippet=gm_store_snippet,
                store_email_body=gm_store_body,
                auto_update_status=gm_auto_update,
            ),
            calendar=CalendarConfig(
                enabled=cal_enabled,
                credentials_path=cal_creds,
                token_path=cal_token,
                calendar_id=cal_calendar_id,
                timezone=cal_tz,
                default_interview_duration_minutes=int(cal_duration),
                create_calendar_events=cal_create_events,
                add_reminders=cal_reminders,
                reminder_minutes_before=[
                    int(x.strip()) for x in cal_reminder_raw.split(",") if x.strip().isdigit()
                ] or [1440, 60],
            ),
        )
        save_settings(updated)
        st.success("Settings saved to `config/settings.yaml`.")


# ---------------------------------------------------------------------------
# Gmail page
# ---------------------------------------------------------------------------


def _page_gmail() -> None:
    st.title("Gmail – read-only integration")

    settings = load_settings()
    cfg = settings.gmail

    # ── 1. Setup status ───────────────────────────────────────────────────
    st.subheader("Setup status")
    from cv_sender.gmail_integration import is_gmail_authenticated, is_gmail_configured  # noqa: PLC0415

    col1, col2 = st.columns(2)
    col1.metric("Gmail enabled", "Yes" if cfg.enabled else "No")
    col1.metric("Credentials file", "Found" if Path(cfg.credentials_path).exists() else "Missing")
    col2.metric("Token (authenticated)", "Yes" if is_gmail_authenticated(cfg) else "No")
    col2.caption(f"Scope: `https://www.googleapis.com/auth/gmail.readonly`  _(read-only)_")

    if not cfg.enabled:
        st.info(
            "Gmail integration is disabled. Enable it in **Settings** under the _Gmail_ section."
        )
        _render_gmail_setup_instructions(cfg)
        return

    if not Path(cfg.credentials_path).exists():
        st.warning(f"Credentials file not found at `{cfg.credentials_path}`.")
        _render_gmail_setup_instructions(cfg)
        return

    # ── 2. Scan controls ─────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Scan Gmail")
    sc1, sc2, sc3 = st.columns(3)
    scan_7 = sc1.button("Scan last 7 days", key="gmail_scan_7")
    scan_30 = sc2.button("Scan last 30 days", key="gmail_scan_30")
    use_llm = sc3.checkbox("Use LM Studio classification", value=False, key="gmail_use_llm")

    if scan_7 or scan_30:
        # Temporarily override scan_days_back for this scan
        scan_days = 7 if scan_7 else 30
        from cv_sender.config import GmailConfig as _GmailConfig  # noqa: PLC0415
        scan_cfg = _GmailConfig(**{**cfg.model_dump(), "scan_days_back": scan_days})

        # Patch settings so run_gmail_scan picks up correct config
        with st.spinner(f"Scanning last {scan_days} days…"):
            from cv_sender.gmail_integration import (  # noqa: PLC0415
                get_gmail_service,
                scan_gmail_for_application_replies,
            )
            from cv_sender.storage import add_email_match, load_email_matches  # noqa: PLC0415

            try:
                service = get_gmail_service(scan_cfg)
                applications = load_applications()
                existing_ids = {m.email_message_id for m in load_email_matches()}
                new_matches = scan_gmail_for_application_replies(
                    service=service,
                    applications=applications,
                    cfg=scan_cfg,
                    existing_message_ids=existing_ids,
                    use_llm=use_llm,
                )
                added = sum(1 for m in new_matches if add_email_match(m))
                skipped = len(new_matches) - added
                st.success(f"Scan complete — {added} new match(es) found, {skipped} duplicate(s) skipped.")
            except ImportError as exc:
                st.error(str(exc))
            except FileNotFoundError as exc:
                st.error(str(exc))
            except Exception as exc:  # noqa: BLE001
                st.error(f"Scan error: {exc}")
        st.rerun()

    # ── 3. Matches table ─────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Email matches")

    from cv_sender.storage import load_email_matches  # noqa: PLC0415
    all_matches = load_email_matches()

    if not all_matches:
        st.info("No email matches yet. Run a scan above.")
    else:
        from cv_sender.listing import ListQuery as _LQg, build_list_result as _blrg, init_list_state as _ilsg, render_pagination_controls as _rpcg  # noqa: PLC0415, E501
        _GM_PFX = "gmail_matches"
        _ilsg(_GM_PFX, {"page_size": 25, "sort_by": "received_at", "sort_dir": "desc"})

        _gm_c1, _gm_c2 = st.columns(2)
        status_filter: str = _gm_c1.selectbox(
            "Filter by match status",
            ["All", "pending", "applied", "ignored"],
            key=f"{_GM_PFX}_status_filter",
        )
        _gm_page_size: int = _gm_c2.selectbox(
            "Per page", [10, 25, 50, 100],
            index=[10, 25, 50, 100].index(st.session_state.get(f"{_GM_PFX}_page_size", 25)),
            key=f"{_GM_PFX}_page_size",
        )

        _gm_watched = (status_filter, _gm_page_size)
        if st.session_state.get(f"{_GM_PFX}_sentinel") != _gm_watched:
            st.session_state[f"{_GM_PFX}_page"] = 1
        st.session_state[f"{_GM_PFX}_sentinel"] = _gm_watched

        def _gm_filter_fn(m) -> bool:  # noqa: ANN001
            return status_filter == "All" or m.status == status_filter

        _gm_q = _LQg(
            page=st.session_state.get(f"{_GM_PFX}_page", 1),
            page_size=_gm_page_size,
            sort_by="received_at",
            sort_dir="desc",
        )
        _gm_result = _blrg(all_matches, _gm_q, filter_fn=_gm_filter_fn)

        _gm_new_page = _rpcg(_GM_PFX, _gm_result)
        if _gm_new_page != st.session_state.get(f"{_GM_PFX}_page"):
            st.session_state[f"{_GM_PFX}_page"] = _gm_new_page
            st.rerun()

        for match in _gm_result.items:
            label = (
                f"**{match.subject or '(no subject)'}**  — {match.from_email}"
                f"  |  {match.classification}  |  {match.received_at.strftime('%Y-%m-%d')}"
            )
            with st.expander(label):
                mc1, mc2, mc3 = st.columns(3)
                mc1.markdown(f"**From:** {match.from_name or '—'} `{match.from_email}`")
                mc1.markdown(f"**Received:** {match.received_at.strftime('%Y-%m-%d %H:%M')}")
                mc2.markdown(f"**Company:** {match.matched_company or '—'}")
                mc2.markdown(f"**Application:** {match.matched_application_title or '—'}")
                mc3.markdown(f"**Match score:** {match.match_score}")
                mc3.markdown(f"**Classification:** `{match.classification}`  (conf: {match.confidence:.0%})")

                if match.snippet:
                    st.caption(f"**Snippet:** {match.snippet[:300]}")

                if match.reasons:
                    st.caption("**Reasons:** " + "  |  ".join(match.reasons))

                st.markdown(f"**Status suggestion:** `{match.status_suggestion}`")

                # Action buttons
                if match.status == "pending":
                    ab1, ab2 = st.columns(2)
                    if ab1.button("Apply suggestion", key=f"gmail_apply_{match.id}"):
                        ok, msg = services.apply_email_match(match.id)
                        st.success(msg) if ok else st.error(msg)
                        st.rerun()
                    if ab2.button("Ignore", key=f"gmail_ignore_{match.id}"):
                        ok, msg = services.ignore_email_match(match.id)
                        st.success(msg) if ok else st.error(msg)
                        st.rerun()
                else:
                    st.caption(f"Status: **{match.status}**")


def _render_gmail_setup_instructions(cfg: Any) -> None:
    st.markdown("---")
    st.subheader("Setup instructions")
    st.markdown(
        f"""
**Step 1 – Create a Google Cloud project**
1. Go to [https://console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (or select an existing one).
3. Enable the **Gmail API** under _APIs & Services → Library_.

**Step 2 – Create OAuth 2.0 credentials**
1. Go to _APIs & Services → Credentials_.
2. Click **Create Credentials → OAuth client ID**.
3. Choose **Desktop app**.
4. Download the JSON file.
5. Save it to: `{cfg.credentials_path}`

**Step 3 – Enable Gmail in settings**
Set `gmail.enabled: true` in `config/settings.yaml` (or via the Settings page).

**Step 4 – First-time authentication**
Click **Scan** — a browser window will open asking you to authorise read-only Gmail access.
The token is saved to `{cfg.token_path}` and reused for future scans.

> The app requests **read-only** scope only: `https://www.googleapis.com/auth/gmail.readonly`
> No emails are sent, deleted, or modified.
"""
    )


# ---------------------------------------------------------------------------
# Interviews page
# ---------------------------------------------------------------------------


def _page_interviews() -> None:
    st.title("Interviews")

    tab_upcoming, tab_past, tab_new = st.tabs(["Upcoming", "Past", "Schedule new"])

    with tab_upcoming:
        upcoming = services.list_upcoming_interviews()
        if not upcoming:
            st.info("No upcoming interviews.")
        else:
            for iv in sorted(upcoming, key=lambda i: i.interview_at):
                iv_dt = iv.interview_at.strftime("%Y-%m-%d %H:%M")
                with st.expander(
                    f"**{iv.company}** — {iv.title}  |  {iv_dt}  |  _{iv.interview_type}_",
                    expanded=True,
                ):
                    c1, c2, c3 = st.columns(3)
                    c1.write(f"**Duration:** {iv.duration_minutes} min")
                    c2.write(f"**Status:** {iv.status}")
                    c3.write(f"**Source:** {iv.source}")
                    if iv.meeting_url:
                        st.write(f"**Meeting URL:** {iv.meeting_url}")
                    if iv.location:
                        st.write(f"**Location:** {iv.location}")
                    if iv.participants:
                        st.write(f"**Participants:** {', '.join(iv.participants)}")
                    if iv.notes:
                        st.write(f"**Notes:** {iv.notes}")
                    if iv.calendar_event_id:
                        st.caption(f"Calendar event: {iv.calendar_event_id}")

                    bc1, bc2, bc3 = st.columns(3)
                    if bc1.button("✅ Completed", key=f"iv_done_{iv.id}"):
                        ok, msg = services.mark_interview_completed(iv.id)
                        st.success(msg) if ok else st.error(msg)
                        st.rerun()
                    if bc2.button("❌ Cancel", key=f"iv_cancel_{iv.id}"):
                        ok, msg = services.cancel_interview(iv.id)
                        st.success(msg) if ok else st.error(msg)
                        st.rerun()

                    with bc3.expander("Reschedule"):
                        new_dt = st.datetime_input(
                            "New datetime", value=iv.interview_at, key=f"iv_reschedule_dt_{iv.id}"
                        )
                        update_cal = st.checkbox(
                            "Update calendar event", value=False, key=f"iv_reschedule_cal_{iv.id}"
                        )
                        if st.button("Confirm reschedule", key=f"iv_reschedule_btn_{iv.id}"):
                            ok, msg = services.reschedule_interview(iv.id, new_dt, update_cal)
                            st.success(msg) if ok else st.error(msg)
                            st.rerun()

    with tab_past:
        past = services.list_past_interviews()
        if not past:
            st.info("No past interviews recorded.")
        else:
            import pandas as pd  # noqa: PLC0415

            rows = [
                {
                    "Company": i.company,
                    "Role": i.title,
                    "Date": i.interview_at.strftime("%Y-%m-%d %H:%M"),
                    "Type": str(i.interview_type),
                    "Status": str(i.status),
                    "Source": i.source,
                }
                for i in sorted(past, key=lambda i: i.interview_at, reverse=True)
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tab_new:
        st.subheader("Schedule a new interview")
        settings = load_settings()
        apps = _safe_load_applications()
        app_options = {f"{a.company} — {a.title} ({a.id[:8]})": a.id for a in apps}
        if not app_options:
            st.warning("No applications found. Add one first.")
        else:
            with st.form("new_interview_form", clear_on_submit=True):
                selected_label = st.selectbox("Application", list(app_options.keys()))
                iv_dt_new = st.datetime_input(
                    "Interview date & time",
                    value=datetime.now(UTC).replace(minute=0, second=0, microsecond=0),
                )
                nc1, nc2 = st.columns(2)
                iv_duration = nc1.number_input(
                    "Duration (min)",
                    min_value=15, max_value=480,
                    value=settings.calendar.default_interview_duration_minutes,
                )
                iv_type = nc2.selectbox(
                    "Interview type",
                    [t.value for t in InterviewType],
                    index=list(InterviewType).index(InterviewType.UNKNOWN),
                )
                iv_url = st.text_input("Meeting URL", placeholder="https://meet.google.com/...")
                iv_location = st.text_input("Location / address", placeholder="Optional")
                iv_notes = st.text_area("Notes", height=80)
                iv_create_cal = st.checkbox(
                    "Create Google Calendar event",
                    value=False,
                    disabled=not settings.calendar.create_calendar_events,
                    help="Requires calendar integration to be enabled with create_calendar_events=true",
                )
                form_submitted = st.form_submit_button("Schedule interview")

            if form_submitted:
                app_id = app_options[selected_label]
                data = {
                    "interview_at": iv_dt_new,
                    "duration_minutes": int(iv_duration),
                    "interview_type": InterviewType(iv_type),
                    "meeting_url": iv_url.strip(),
                    "location": iv_location.strip(),
                    "notes": iv_notes.strip(),
                    "source": "manual",
                }
                ok, msg, iv = services.create_interview(app_id, data, iv_create_cal)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
                st.rerun()


# ---------------------------------------------------------------------------
# Analytics page
# ---------------------------------------------------------------------------


def _page_analytics() -> None:  # noqa: PLR0912, PLR0914, PLR0915
    st.title("Analytics")

    try:
        import pandas as pd  # noqa: PLC0415
        _has_pandas = True
    except ImportError:
        _has_pandas = False

    from cv_sender.analytics import (  # noqa: PLC0415
        AnalyticsData,
        build_llm_analytics_prompt,
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
        load_analytics_data,
    )

    raw = load_analytics_data()
    settings = load_settings()

    # ── Filters ──────────────────────────────────────────────────────────
    with st.expander("🔍 Filters", expanded=False):
        fc1, fc2 = st.columns(2)
        all_sources = sorted({a.source for a in raw.applications if a.source})
        all_cvs = sorted({a.selected_cv_name for a in raw.applications if a.selected_cv_name})
        all_statuses = sorted({a.status for a in raw.applications})
        all_techs: list[str] = sorted(
            {t for o in raw.offers for t in o.technologies if t}
        )

        sel_sources = fc1.multiselect("Source", all_sources)
        sel_cvs = fc2.multiselect("CV profile", all_cvs)
        sc1, sc2 = st.columns(2)
        sel_statuses = sc1.multiselect("Status", all_statuses)
        sel_techs = sc2.multiselect("Technology", all_techs)
        dc1, dc2 = st.columns(2)
        date_from = dc1.date_input("Sent from", value=None)
        date_to = dc2.date_input("Sent to", value=None)
        sl1, sl2 = st.columns(2)
        salary_floor = sl1.number_input("Min salary ≥", value=0, step=1000)
        salary_ceil = sl2.number_input("Max salary ≤ (0 = no limit)", value=0, step=1000)

    # Build filtered app list
    from cv_sender.analytics import _filter_apps  # noqa: PLC0415

    filter_kwargs: dict = {}
    if sel_sources:
        filter_kwargs["sources"] = sel_sources
    if sel_cvs:
        filter_kwargs["cv_names"] = sel_cvs
    if sel_statuses:
        filter_kwargs["statuses"] = sel_statuses
    if sel_techs:
        filter_kwargs["technologies"] = sel_techs
    if date_from:
        filter_kwargs["date_from"] = datetime(date_from.year, date_from.month, date_from.day, tzinfo=UTC)
    if date_to:
        filter_kwargs["date_to"] = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=UTC)
    if salary_floor and salary_floor > 0:
        filter_kwargs["salary_min_floor"] = float(salary_floor)
    if salary_ceil and salary_ceil > 0:
        filter_kwargs["salary_max_ceil"] = float(salary_ceil)

    apps = _filter_apps(raw, **filter_kwargs)

    # ── Pre-compute ───────────────────────────────────────────────────────
    funnel = calculate_funnel_metrics(raw, apps)
    rates = calculate_response_rates(raw, apps)
    tm = calculate_time_metrics(raw, apps)
    source_rows = calculate_source_performance(raw, apps)
    cv_rows = calculate_cv_performance(raw, apps)
    tech_rows = calculate_technology_performance(raw, apps)
    salary = calculate_salary_analysis(raw, apps)
    weekly = calculate_weekly_activity(raw, apps)
    insights = generate_deterministic_insights(funnel, rates, tm, source_rows, cv_rows, salary, weekly)

    st.caption(f"Showing metrics for **{len(apps)}** application(s) matching filters.")

    # ── 1. Funnel ────────────────────────────────────────────────────────
    st.subheader("Funnel")
    fc = st.columns(9)
    labels = [
        ("Imported", funnel.imported),
        ("Apply", funnel.apply_scored),
        ("Maybe", funnel.maybe_scored),
        ("Ready", funnel.ready_to_send),
        ("Sent", funnel.sent),
        ("Replies", funnel.reply_received),
        ("Interview", funnel.interview),
        ("Offer", funnel.offer),
        ("Rejected", funnel.rejected),
    ]
    for col, (lbl, val) in zip(fc, labels):
        col.metric(lbl, val)

    # ── 2. Response rates ─────────────────────────────────────────────────
    st.subheader("Response rates")
    rc1, rc2, rc3, rc4 = st.columns(4)
    rc1.metric("Response rate", f"{rates.response_rate():.1f}%", help="replies / sent")
    rc2.metric("Interview rate", f"{rates.interview_rate():.1f}%", help="interviews / sent")
    rc3.metric("Rejection rate", f"{rates.rejection_rate():.1f}%", help="rejections / sent")
    rc4.metric("Offer rate", f"{rates.offer_rate():.1f}%", help="offers / sent")

    # ── 3. Time metrics ───────────────────────────────────────────────────
    st.subheader("Time metrics")
    tc1, tc2, tc3, tc4, tc5 = st.columns(5)
    tc1.metric(
        "Avg days to reply",
        f"{tm.avg_days_to_reply:.1f}" if tm.avg_days_to_reply is not None else "—",
    )
    tc2.metric(
        "Median days to reply",
        f"{tm.median_days_to_reply:.1f}" if tm.median_days_to_reply is not None else "—",
    )
    tc3.metric(
        "Avg days to interview",
        f"{tm.avg_days_to_interview:.1f}" if tm.avg_days_to_interview is not None else "—",
    )
    tc4.metric("Sent (last 7 days)", tm.sent_last_7)
    tc5.metric("Sent (last 30 days)", tm.sent_last_30)

    # ── 8. Weekly activity ────────────────────────────────────────────────
    if weekly:
        st.subheader("Weekly activity")
        if _has_pandas:
            import pandas as pd  # noqa: PLC0415

            df_week = pd.DataFrame(weekly).set_index("week")
            st.bar_chart(df_week[["sent", "replies", "interviews"]])
        else:
            for row in weekly[-12:]:
                st.write(f"**{row['week']}** — sent: {row['sent']}, replies: {row['replies']}, interviews: {row['interviews']}")

    # ── 4. Source performance ─────────────────────────────────────────────
    if source_rows:
        st.subheader("Source performance")
        if _has_pandas:
            import pandas as pd  # noqa: PLC0415

            st.dataframe(pd.DataFrame(source_rows), use_container_width=True, hide_index=True)
        else:
            st.table(source_rows)

    # ── 5. CV profile performance ─────────────────────────────────────────
    if cv_rows:
        st.subheader("CV profile performance")
        if _has_pandas:
            import pandas as pd  # noqa: PLC0415

            st.dataframe(pd.DataFrame(cv_rows), use_container_width=True, hide_index=True)
        else:
            st.table(cv_rows)

    # ── 6. Technology performance ─────────────────────────────────────────
    if tech_rows:
        st.subheader("Technology performance")
        if _has_pandas:
            import pandas as pd  # noqa: PLC0415

            st.dataframe(
                pd.DataFrame(tech_rows[:30]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.table(tech_rows[:30])

    # ── 7. Salary analysis ────────────────────────────────────────────────
    st.subheader("Salary analysis")
    sa1, sa2, sa3, sa4 = st.columns(4)
    sa1.metric(
        "Avg sent salary min",
        f"{salary.avg_sent_salary_min:,.0f}" if salary.avg_sent_salary_min else "—",
    )
    sa2.metric(
        "Avg sent salary max",
        f"{salary.avg_sent_salary_max:,.0f}" if salary.avg_sent_salary_max else "—",
    )
    sa3.metric(
        "Avg reply salary min",
        f"{salary.avg_reply_salary_min:,.0f}" if salary.avg_reply_salary_min else "—",
    )
    sa4.metric(
        "Avg interview salary min",
        f"{salary.avg_interview_salary_min:,.0f}" if salary.avg_interview_salary_min else "—",
    )
    if salary.bucket_rows:
        if _has_pandas:
            import pandas as pd  # noqa: PLC0415

            st.dataframe(
                pd.DataFrame(salary.bucket_rows), use_container_width=True, hide_index=True
            )
        else:
            st.table(salary.bucket_rows)

    # ── 9. Insights ───────────────────────────────────────────────────────
    st.subheader("💡 Insights")
    for insight in insights:
        st.info(insight)

    # Optional LLM summary
    if settings.lm_studio.enabled:
        if st.button("🤖 Generate AI summary"):
            prompt = build_llm_analytics_prompt(funnel, rates, tm, source_rows, cv_rows)
            try:
                from cv_sender.llm import get_llm_score  # noqa: PLC0415

                # Use the LLM via direct HTTP call
                import httpx  # noqa: PLC0415

                resp = httpx.post(
                    f"{settings.lm_studio.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.lm_studio.api_key}"},
                    json={
                        "model": settings.lm_studio.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                summary = resp.json()["choices"][0]["message"]["content"]
                st.markdown("**AI Summary:**")
                st.write(summary)
            except Exception as exc:  # noqa: BLE001
                st.error(f"LLM request failed: {exc}")

    # ── 11. Export ────────────────────────────────────────────────────────
    st.subheader("Export")
    csv_str = export_analytics_csv(source_rows, cv_rows, weekly, tech_rows)
    st.download_button(
        label="📥 Export analytics CSV",
        data=csv_str,
        file_name="analytics_export.csv",
        mime="text/csv",
    )


# ---------------------------------------------------------------------------
# Bookmarklet page
# ---------------------------------------------------------------------------



def _page_rapid_apply() -> None:  # noqa: PLR0912, PLR0914, PLR0915
    """Focused one-at-a-time apply session for the rapid apply queue."""
    st.title("Rapid Apply Session")

    from cv_sender.apply_queue import (  # noqa: PLC0415
        advance_session,
        build_apply_queue_from_offers,
        get_active_queue_items,
    )
    from cv_sender.rapid_apply_service import (  # noqa: PLC0415
        SKIP_REASONS,
        QualityStatus,
        do_fill,
        do_mark_sent,
        do_skip,
        get_session_stats,
    )
    from cv_sender.storage import get_offer_by_id, get_queue_item_by_id  # noqa: PLC0415

    # ------------------------------------------------------------------ #
    # Session state bootstrap
    # ------------------------------------------------------------------ #
    if "ra_item_id" not in st.session_state:
        st.session_state.ra_item_id = None
    if "ra_started_at" not in st.session_state:
        st.session_state.ra_started_at = None
    if "ra_processed" not in st.session_state:
        st.session_state.ra_processed = 0
    if "ra_last_action" not in st.session_state:
        st.session_state.ra_last_action = ""
    if "ra_fill_result" not in st.session_state:
        st.session_state.ra_fill_result = None
    if "ra_quality" not in st.session_state:
        st.session_state.ra_quality = None
    if "ra_min_score" not in st.session_state:
        st.session_state.ra_min_score = 0
    if "ra_source_filter" not in st.session_state:
        st.session_state.ra_source_filter = ""
    if "ra_exclude_failed" not in st.session_state:
        st.session_state.ra_exclude_failed = False
    # Campaign integration: optional campaign_id set from Campaigns page
    if "ra_campaign_id" not in st.session_state:
        st.session_state.ra_campaign_id = ""

    campaign_id: str = st.session_state.ra_campaign_id or ""

    def _filter_kwargs() -> dict:
        return dict(
            min_score=st.session_state.ra_min_score or None,
            source_filter=st.session_state.ra_source_filter or None,
            exclude_failed=st.session_state.ra_exclude_failed,
        )

    # ------------------------------------------------------------------ #
    # Campaign progress banner (when running inside a campaign)
    # ------------------------------------------------------------------ #
    if campaign_id:
        from cv_sender.campaigns import get_campaign, get_campaign_progress  # noqa: PLC0415

        campaign_obj = get_campaign(campaign_id)
        c_progress = get_campaign_progress(campaign_id)
        if campaign_obj and c_progress:
            with st.container(border=True):
                st.markdown(f"**Campaign: {campaign_obj.name}**")
                camp_cols = st.columns(5)
                camp_cols[0].metric("Target", c_progress.target)
                camp_cols[1].metric("Sent", c_progress.sent)
                camp_cols[2].metric("Remaining", c_progress.remaining)
                camp_cols[3].metric("Queued", c_progress.queued_available)
                camp_cols[4].metric("Progress", f"{c_progress.progress_pct:.0f}%")
                st.progress(min(1.0, c_progress.progress_pct / 100))
                if c_progress.remaining == 0:
                    st.success("Target reached! Great work.")
                elif c_progress.queue_shortage:
                    st.warning(c_progress.queue_shortage_message)
            if st.button("Exit campaign mode"):
                st.session_state.ra_campaign_id = ""
                st.rerun()
        st.divider()

    # ------------------------------------------------------------------ #
    # 1. Session summary bar
    # ------------------------------------------------------------------ #
    stats = get_session_stats()
    all_active_items = get_active_queue_items(**_filter_kwargs())
    # When running inside a campaign, restrict to campaign items only
    if campaign_id:
        active_items = [i for i in all_active_items if i.campaign_id == campaign_id]
    else:
        active_items = all_active_items

    summary_cols = st.columns(6)
    summary_cols[0].metric("Queued", stats.queued)
    summary_cols[1].metric("Filled", stats.filled)
    summary_cols[2].metric("Sent", stats.sent)
    summary_cols[3].metric("Skipped", stats.skipped)
    summary_cols[4].metric("Failed", stats.failed)
    summary_cols[5].metric("Session processed", st.session_state.ra_processed)

    st.divider()

    # ------------------------------------------------------------------ #
    # Empty queue → call-to-action
    # ------------------------------------------------------------------ #
    if not active_items:
        st.info("The apply queue is empty.")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Run Emergency React Search + Build Queue", type="primary"):
                from cv_sender.collectors.base import JobSearchCriteria  # noqa: PLC0415
                from cv_sender.job_search import collect_jobs  # noqa: PLC0415

                criteria = JobSearchCriteria.emergency_react()
                sources = ["justjoin", "rocketjobs", "nofluffjobs", "pracuj"]
                with st.spinner("Collecting React / Frontend offers …"):
                    collect_jobs(criteria, mode="playwright", source_names=sources, auto_score=True)
                with st.spinner("Building queue …"):
                    build_apply_queue_from_offers()
                st.success("Done! Refresh to see the new queue items.")
                st.rerun()
        with col2:
            if st.button("Build Queue from existing offers"):
                with st.spinner("Building queue …"):
                    build_apply_queue_from_offers()
                st.success("Queue rebuilt.")
                st.rerun()
        return

    # ------------------------------------------------------------------ #
    # Resolve current item
    # ------------------------------------------------------------------ #
    current_item = None
    if st.session_state.ra_item_id:
        current_item = get_queue_item_by_id(st.session_state.ra_item_id)
        # If the item is now in a terminal state, auto-advance
        from cv_sender.models import ApplyQueueItemStatus  # noqa: PLC0415

        if current_item and current_item.status in {
            ApplyQueueItemStatus.SENT,
            ApplyQueueItemStatus.SKIPPED,
        }:
            current_item = None

    if current_item is None:
        current_item = active_items[0] if active_items else None
        if current_item:
            st.session_state.ra_item_id = current_item.id
            st.session_state.ra_fill_result = None
            st.session_state.ra_quality = None
            if st.session_state.ra_started_at is None:
                from datetime import UTC, datetime  # noqa: PLC0415

                st.session_state.ra_started_at = datetime.now(UTC).isoformat()

    if current_item is None:
        st.success("All items processed! Great work.")
        return

    # Position indicator
    position = next(
        (i + 1 for i, item in enumerate(active_items) if item.id == current_item.id),
        1,
    )
    st.caption(f"Item {position} of {len(active_items)} active • session started {st.session_state.ra_started_at or 'now'}")

    # ------------------------------------------------------------------ #
    # Filters sidebar (compact)
    # ------------------------------------------------------------------ #
    with st.sidebar:
        st.subheader("Session filters")
        st.session_state.ra_min_score = st.number_input(
            "Min score", min_value=0, max_value=100,
            value=st.session_state.ra_min_score, step=5,
        )
        all_sources = sorted({i.source for i in get_active_queue_items()})
        src_options = ["(any)"] + all_sources
        cur_src = st.session_state.ra_source_filter or "(any)"
        chosen_src = st.selectbox("Source", src_options, index=src_options.index(cur_src) if cur_src in src_options else 0)
        st.session_state.ra_source_filter = "" if chosen_src == "(any)" else chosen_src
        st.session_state.ra_exclude_failed = st.checkbox(
            "Exclude failed items",
            value=st.session_state.ra_exclude_failed,
        )

    # ------------------------------------------------------------------ #
    # 2. Current offer card
    # ------------------------------------------------------------------ #
    offer = get_offer_by_id(current_item.offer_id)
    with st.container(border=True):
        c1, c2 = st.columns([3, 1])
        with c1:
            st.subheader(f"{current_item.title}")
            st.markdown(f"**{current_item.company}** · {current_item.source}")
            if current_item.url:
                st.markdown(f"🔗 [{current_item.url}]({current_item.url})")
        with c2:
            score_val = current_item.score or 0
            score_color = "green" if score_val >= 75 else ("orange" if score_val >= 50 else "red")
            st.markdown(
                f"<div style='text-align:center'>"
                f"<span style='font-size:2rem;font-weight:bold;color:{score_color}'>{score_val}</span>"
                f"<br><small>score</small><br>"
                f"<span style='font-size:1.1rem'>{current_item.priority_score:.1f}</span>"
                f"<br><small>priority</small></div>",
                unsafe_allow_html=True,
            )

        if offer:
            detail_cols = st.columns(3)
            with detail_cols[0]:
                if offer.location:
                    st.markdown(f"📍 {offer.location}")
                if offer.contract:
                    st.markdown(f"📄 {offer.contract}")
            with detail_cols[1]:
                if offer.salary_min or offer.salary_max:
                    sal = f"{offer.salary_min or '?'} – {offer.salary_max or '?'} {offer.currency}"
                    st.markdown(f"💰 {sal}")
                if offer.decision:
                    st.markdown(f"🎯 Decision: **{offer.decision}**")
            with detail_cols[2]:
                if current_item.selected_cv_name:
                    st.markdown(f"📋 CV: {current_item.selected_cv_name}")
                status_badge = {
                    "queued": "🟡", "in_progress": "🔵", "filled": "🟢",
                    "failed": "🔴", "sent": "✅", "skipped": "⏭️",
                }.get(current_item.status, "⚪")
                st.markdown(f"{status_badge} Status: **{current_item.status}**")

        if current_item.reasons:
            st.markdown("**Match reasons:** " + " · ".join(current_item.reasons[:4]))
        if current_item.warnings:
            st.warning("⚠️ " + " · ".join(current_item.warnings[:3]))

    # ------------------------------------------------------------------ #
    # Show fill result if available
    # ------------------------------------------------------------------ #
    if st.session_state.ra_fill_result is not None:
        result = st.session_state.ra_fill_result
        quality: QualityStatus = st.session_state.ra_quality

        badge_map = {"ready": "🟢 Ready", "review_needed": "🟡 Review needed", "not_ready": "🔴 Not ready"}
        st.markdown(f"**Quality: {badge_map.get(quality.badge, quality.badge)}**")

        fill_cols = st.columns(2)
        with fill_cols[0]:
            for line in quality.checklist:
                st.markdown(line)
        with fill_cols[1]:
            for w in quality.warnings:
                st.warning(w)

        if result.generated_answers:
            with st.expander(f"Generated answers ({len(result.generated_answers)})"):
                for ans in result.generated_answers:
                    q = ans.get("question", "?")
                    a = ans.get("answer", "")
                    st.markdown(f"**Q:** {q}")
                    st.markdown(f"**A:** {a}")
                    st.divider()

        if result.debug_run_id:
            st.caption(f"Debug run ID: `{result.debug_run_id}` — check the Debug page for details.")

        if result.status != "failed":
            st.info(
                "✋ **Application form has been filled.  "
                "Please review it manually in the browser before submitting.**"
            )

    # ------------------------------------------------------------------ #
    # Last action feedback
    # ------------------------------------------------------------------ #
    if st.session_state.ra_last_action:
        st.success(st.session_state.ra_last_action)
        st.session_state.ra_last_action = ""

    # ------------------------------------------------------------------ #
    # 3. Primary action buttons
    # ------------------------------------------------------------------ #
    st.markdown("---")
    st.caption("**Keyboard hints:** 1 Fill · 2 Mark sent · 3 Skip")

    btn_cols = st.columns(6)

    # ——— Fill ———
    with btn_cols[0]:
        fill_label = "🔄 Retry fill" if current_item.status == "failed" else "1️⃣ Fill this application"
        if st.button(fill_label, type="primary", use_container_width=True):
            with st.spinner("Filling form …"):
                action_result = do_fill(current_item.id)
            st.session_state.ra_fill_result = action_result.fill_result
            st.session_state.ra_quality = action_result.quality
            st.session_state.ra_processed += 1
            st.session_state.ra_last_action = f"Fill result: {action_result.fill_result.status}"
            st.rerun()

    # ——— Retry generic ———
    if current_item.status == "failed":
        with btn_cols[1]:
            if st.button("🔁 Retry (Generic)", use_container_width=True):
                with st.spinner("Retrying with generic filler …"):
                    action_result = do_fill(current_item.id, force_generic=True)
                st.session_state.ra_fill_result = action_result.fill_result
                st.session_state.ra_quality = action_result.quality
                st.session_state.ra_last_action = f"Generic retry result: {action_result.fill_result.status}"
                st.rerun()

    # ——— Mark sent ———
    with btn_cols[2]:
        if st.button("2️⃣ Mark as sent", use_container_width=True):
            ms = do_mark_sent(current_item.id, **_filter_kwargs())
            st.session_state.ra_processed += 1
            if campaign_id:
                from cv_sender.campaigns import mark_campaign_sent  # noqa: PLC0415
                mark_campaign_sent(
                    campaign_id,
                    application_id=ms.next_item.id if ms.next_item else "",
                    offer_id=current_item.offer_id,
                    queue_item_id=current_item.id,
                )
            if ms.next_item:
                st.session_state.ra_item_id = ms.next_item.id
            else:
                st.session_state.ra_item_id = None
            st.session_state.ra_fill_result = None
            st.session_state.ra_quality = None
            st.session_state.ra_last_action = ms.message
            st.rerun()

    # ——— Skip (with reason popover) ———
    with btn_cols[3]:
        if st.button("3️⃣ Skip", use_container_width=True):
            st.session_state["ra_show_skip"] = True

    # ——— Open offer ———
    with btn_cols[4]:
        if current_item.url:
            st.link_button("🌐 Open offer", current_item.url, use_container_width=True)

    # ——— Next (no status change) ———
    with btn_cols[5]:
        if st.button("⏭️ Next", use_container_width=True):
            next_item = advance_session(current_item.id, **_filter_kwargs())
            if next_item:
                st.session_state.ra_item_id = next_item.id
            else:
                st.session_state.ra_item_id = None
            st.session_state.ra_fill_result = None
            st.session_state.ra_quality = None
            st.rerun()

    # ——— Skip reason modal ———
    if st.session_state.get("ra_show_skip"):
        with st.form("skip_form"):
            st.markdown("**Skip reason** (optional)")
            reason_choice = st.selectbox("Reason", ["(none)"] + SKIP_REASONS)
            if st.form_submit_button("Confirm skip"):
                reason = "" if reason_choice == "(none)" else reason_choice
                sk = do_skip(current_item.id, reason=reason, **_filter_kwargs())
                st.session_state.ra_processed += 1
                if campaign_id:
                    from cv_sender.campaigns import record_campaign_activity  # noqa: PLC0415
                    from cv_sender.models import CampaignActivityType  # noqa: PLC0415
                    record_campaign_activity(
                        campaign_id,
                        CampaignActivityType.SKIPPED,
                        offer_id=current_item.offer_id,
                        queue_item_id=current_item.id,
                        note=reason,
                    )
                if sk.next_item:
                    st.session_state.ra_item_id = sk.next_item.id
                else:
                    st.session_state.ra_item_id = None
                st.session_state.ra_fill_result = None
                st.session_state.ra_quality = None
                st.session_state.ra_last_action = sk.message
                st.session_state["ra_show_skip"] = False
                st.rerun()
            if st.form_submit_button("Cancel"):
                st.session_state["ra_show_skip"] = False
                st.rerun()

    # ——— Stop session ———
    st.markdown("---")
    if st.button("🛑 Stop session", help="Exit the focused session view"):
        st.session_state.ra_item_id = None
        st.session_state.ra_started_at = None
        st.session_state.ra_processed = 0
        st.session_state.ra_fill_result = None
        st.session_state.ra_quality = None
        st.session_state.ra_last_action = ""
        st.session_state.ra_campaign_id = ""
        st.info("Session stopped. Navigate to Job Search to manage the queue.")


def _page_job_search() -> None:  # noqa: PLR0912, PLR0914, PLR0915
    st.title("Job Search — Rapid Apply")

    from cv_sender.apply_queue import (  # noqa: PLC0415
        build_apply_queue_from_offers,
        get_queue_stats,
        mark_queue_item_status,
        remove_from_queue,
    )
    from cv_sender.collectors.base import JobSearchCriteria  # noqa: PLC0415
    from cv_sender.config import load_settings, save_settings  # noqa: PLC0415
    from cv_sender.job_search import run_job_collection  # noqa: PLC0415
    from cv_sender.models import ApplyQueueItemStatus  # noqa: PLC0415
    from cv_sender.storage import load_apply_queue  # noqa: PLC0415

    settings = load_settings()
    cfg = settings.job_search

    # ------------------------------------------------------------------ #
    # Tab layout
    # ------------------------------------------------------------------ #
    tab_search, tab_playwright, tab_queue = st.tabs([
        "Search Criteria & Collection",
        "Playwright Browser Collector",
        "Rapid Apply Queue",
    ])

    # ================================================================== #
    # Tab 1 — Search criteria
    # ================================================================== #
    with tab_search:
        st.subheader("Search criteria")

        col_a, col_b = st.columns(2)
        with col_a:
            enabled = st.checkbox("Job search enabled", value=cfg.enabled)
            emergency = st.checkbox(
                "Emergency React/Frontend mode",
                value=False,
                help="Pre-fill criteria for React / TypeScript / Next.js roles",
            )
            if emergency:
                preset = JobSearchCriteria.emergency_react()
                keywords_default = ", ".join(preset.keywords)
                techs_default = ", ".join(preset.technologies)
                locs_default = ", ".join(preset.locations)
                senior_default = ", ".join(preset.seniority)
                contracts_default = ", ".join(preset.contract_types)
                exclude_default = ", ".join(preset.exclude_keywords)
            else:
                keywords_default = ", ".join(cfg.keywords)
                techs_default = ", ".join(cfg.technologies)
                locs_default = ", ".join(cfg.locations)
                senior_default = ", ".join(cfg.seniority)
                contracts_default = ", ".join(cfg.contract_types)
                exclude_default = ", ".join(cfg.exclude_keywords)

            keywords_raw = st.text_input("Role keywords (comma-separated)", value=keywords_default)
            technologies_raw = st.text_input("Technologies (comma-separated)", value=techs_default)

        with col_b:
            locations_raw = st.text_input("Locations (comma-separated)", value=locs_default)
            seniority_raw = st.text_input("Seniority levels (comma-separated)", value=senior_default)
            contracts_raw = st.text_input("Contract types (comma-separated)", value=contracts_default)
            exclude_raw = st.text_input("Exclude keywords (comma-separated)", value=exclude_default)

        col_c, col_d = st.columns(2)
        with col_c:
            min_salary = st.number_input(
                "Min salary B2B (0 = no minimum)",
                min_value=0,
                step=1000,
                value=cfg.min_salary_b2b,
            )
            require_salary = st.checkbox("Require salary visible", value=cfg.require_salary)
        with col_d:
            max_per_source = st.number_input(
                "Max offers per source",
                min_value=1,
                max_value=200,
                value=cfg.max_offers_per_source,
            )
            max_total = st.number_input(
                "Max total offers",
                min_value=1,
                max_value=1000,
                value=cfg.max_total_offers,
            )

        st.subheader("Sources")
        source_cols = st.columns(5)
        src_names = ["justjoin", "rocketjobs", "nofluffjobs", "pracuj", "linkedin"]
        src_enabled: dict[str, bool] = {}
        for i, name in enumerate(src_names):
            with source_cols[i]:
                default_val = cfg.sources.get(name, None)
                default_enabled = default_val.enabled if default_val else (name != "linkedin")
                src_enabled[name] = st.checkbox(name, value=default_enabled)

        st.subheader("Collector mode")
        mode_map = {
            "Playwright browser collector": "playwright",
            "API/static collector": "api_static",
            "Hybrid: API/static first, then Playwright fallback": "hybrid",
        }
        rev_mode_map = {v: k for k, v in mode_map.items()}
        cfg_mode = (getattr(cfg, "collector_mode", "playwright") or "playwright").lower()
        if cfg_mode in ("api", "static"):
            cfg_mode = "api_static"

        if emergency and st.session_state.get("js_collector_mode") in (None, "", cfg_mode):
            st.session_state["js_collector_mode"] = "playwright"
        elif "js_collector_mode" not in st.session_state:
            st.session_state["js_collector_mode"] = cfg_mode or "playwright"

        selected_mode_label = st.selectbox(
            "Collector mode:",
            list(mode_map.keys()),
            index=(
                list(mode_map.values()).index(st.session_state.get("js_collector_mode", "playwright"))
                if st.session_state.get("js_collector_mode", "playwright") in mode_map.values()
                else 0
            ),
        )
        selected_collector_mode = mode_map[selected_mode_label]
        st.session_state["js_collector_mode"] = selected_collector_mode
        fallback_to_playwright = st.checkbox(
            "Enable Playwright fallback in hybrid mode",
            value=bool(getattr(cfg, "fallback_to_playwright", True)),
        )
        st.caption("Playwright opens public listing pages in a browser and collects job URLs.")
        st.caption("API/static is faster but may return 0 if endpoints changed.")
        st.caption("Hybrid tries API/static first and falls back to Playwright if no results.")

        col_save, col_collect = st.columns(2)
        with col_save:
            if st.button("Save criteria"):
                from cv_sender.config import JobSearchSourceConfig  # noqa: PLC0415

                def _split(s: str) -> list[str]:
                    return [x.strip() for x in s.split(",") if x.strip()]

                cfg_new = cfg.model_copy(
                    update={
                        "enabled": enabled,
                        "collector_mode": selected_collector_mode,
                        "fallback_to_playwright": bool(fallback_to_playwright),
                        "keywords": _split(keywords_raw),
                        "technologies": _split(technologies_raw),
                        "locations": _split(locations_raw),
                        "seniority": _split(seniority_raw),
                        "contract_types": _split(contracts_raw),
                        "exclude_keywords": _split(exclude_raw),
                        "min_salary_b2b": int(min_salary),
                        "require_salary": require_salary,
                        "max_offers_per_source": int(max_per_source),
                        "max_total_offers": int(max_total),
                        "sources": {
                            n: JobSearchSourceConfig(enabled=v) for n, v in src_enabled.items()
                        },
                    }
                )
                new_settings = settings.model_copy(update={"job_search": cfg_new})
                save_settings(new_settings)
                st.success("Criteria saved.")

        with col_collect:
            if st.button("Collect offers now", type="primary"):
                def _split(s: str) -> list[str]:  # noqa: F811
                    return [x.strip() for x in s.split(",") if x.strip()]

                criteria = JobSearchCriteria(
                    keywords=_split(keywords_raw),
                    technologies=_split(technologies_raw),
                    locations=_split(locations_raw),
                    seniority=_split(seniority_raw),
                    contract_types=_split(contracts_raw),
                    min_salary_b2b=int(min_salary),
                    require_salary=require_salary,
                    max_offers_per_source=int(max_per_source),
                    max_total_offers=int(max_total),
                    exclude_keywords=_split(exclude_raw),
                )
                active_sources = [n for n, v in src_enabled.items() if v]

                with st.spinner(f"Collecting from: {', '.join(active_sources)} …"):
                    from cv_sender.job_search import collect_jobs  # noqa: PLC0415

                    use_mode = selected_collector_mode
                    if emergency and st.session_state.get("js_collector_mode") == cfg_mode:
                        use_mode = "playwright"

                    report = collect_jobs(
                        criteria,
                        mode=use_mode,
                        source_names=active_sources,
                        auto_score=True,
                    )

                st.session_state["js_last_report"] = report
                st.success(
                    f"Done! Found: **{report.total_found}** · "
                    f"Imported: **{report.total_accepted}** · "
                    f"Duplicates: {report.total_duplicates} · "
                    f"Rejected: {report.total_rejected}"
                )

        # ---- Diagnostics panel (persists across reruns via session state) ----
        report = st.session_state.get("js_last_report")
        if report is None:
            from cv_sender.collector_diagnostics import get_latest_collection_diagnostics  # noqa: PLC0415
            report = get_latest_collection_diagnostics()

        if report is not None:
            st.subheader("Last collection report")
            st.caption(f"Run {report.run_id[:8]}… · {report.started_at.strftime('%Y-%m-%d %H:%M')}")

            # Per-source summary
            if report.source_summaries:
                import pandas as pd  # noqa: PLC0415
                src_rows = [
                    {
                        "Source": ss.source,
                        "Collector": ss.collector_used,
                        "Status": ss.status,
                        "Raw": ss.raw_found_count,
                        "Job URLs": ss.job_offer_url_count or ss.found_count,
                        "Found": ss.found_count,
                        "Imported": ss.imported_count or ss.accepted_count,
                        "Skipped": ss.skipped_count or ss.rejected_count,
                        "Duplicates": ss.duplicate_count,
                        "Failed": ss.failed_count,
                        "Time (s)": ss.duration_seconds,
                        "Error": ss.error or "",
                    }
                    for ss in report.source_summaries
                ]
                st.dataframe(pd.DataFrame(src_rows), use_container_width=True)

            # Suggestions
            if report.suggestions:
                with st.expander("Filter diagnostics & suggestions", expanded=True):
                    for s in report.suggestions:
                        st.info(f"💡 {s}")

            # Rejected / skipped offers
            rejected = [d for d in report.decisions if d.decision in ("rejected", "needs_review", "failed")]
            if rejected:
                with st.expander(f"Rejected / skipped offers ({len(rejected)})", expanded=False):
                    from cv_sender.listing import ListQuery as _LQ, build_list_result as _blr, init_list_state as _ils, render_pagination_controls as _rpc  # noqa: PLC0415, E501
                    _REJ_PFX = "coll_rejected"
                    _ils(_REJ_PFX, {"page_size": 25, "sort_by": "source", "sort_dir": "asc"})
                    _rej_sources = sorted({d.source for d in rejected if d.source})
                    _rej_c1, _rej_c2 = st.columns(2)
                    _rej_filter_src: str = _rej_c1.selectbox(
                        "Source", ["(all)"] + _rej_sources, key=f"{_REJ_PFX}_filter_src"
                    )
                    _rej_filter_dec: str = _rej_c2.selectbox(
                        "Decision", ["(all)", "rejected", "needs_review", "failed"],
                        key=f"{_REJ_PFX}_filter_dec"
                    )
                    _rej_watched = (_rej_filter_src, _rej_filter_dec)
                    if st.session_state.get(f"{_REJ_PFX}_sentinel") != _rej_watched:
                        st.session_state[f"{_REJ_PFX}_page"] = 1
                    st.session_state[f"{_REJ_PFX}_sentinel"] = _rej_watched

                    def _rej_fn(d) -> bool:  # noqa: ANN001
                        if _rej_filter_src != "(all)" and (d.source or "") != _rej_filter_src:
                            return False
                        if _rej_filter_dec != "(all)" and d.decision != _rej_filter_dec:
                            return False
                        return True

                    _rej_q = _LQ(
                        page=st.session_state.get(f"{_REJ_PFX}_page", 1),
                        page_size=25,
                        sort_by="source",
                        sort_dir="asc",
                    )
                    _rej_result = _blr(rejected, _rej_q, filter_fn=_rej_fn)
                    _rej_new_page = _rpc(_REJ_PFX, _rej_result)
                    if _rej_new_page != st.session_state.get(f"{_REJ_PFX}_page"):
                        st.session_state[f"{_REJ_PFX}_page"] = _rej_new_page
                        st.rerun()

                    for d in _rej_result.items:
                        with st.container(border=True):
                            c1, c2, c3 = st.columns([3, 2, 1])
                            with c1:
                                st.markdown(f"**{d.title}** — {d.company} ({d.source})")
                                if d.url:
                                    st.markdown(f"[{d.url[:60]}…]({d.url})" if len(d.url) > 60 else f"[{d.url}]({d.url})")
                            with c2:
                                st.markdown(f"Decision: **{d.decision}**")
                                if d.reasons:
                                    st.caption("Reasons: " + ", ".join(d.reasons))
                                if d.matched_technologies:
                                    st.caption("Techs: " + ", ".join(d.matched_technologies))
                                st.caption(f"Salary: {d.salary_status}")
                                if d.error:
                                    st.caption(f"Error: {d.error}")
                            with c3:
                                if st.button("Import anyway", key=f"fi_{d.id}"):
                                    from cv_sender.collector_diagnostics import force_import_collected_offer  # noqa: PLC0415
                                    ok, msg = force_import_collected_offer(d, auto_score=True)
                                    if ok:
                                        st.success(msg)
                                    else:
                                        st.error(msg)
                                if d.url:
                                    st.link_button("Open", d.url)

    # ================================================================== #
    # Tab 2 — Playwright Browser Collector
    # ================================================================== #
    with tab_playwright:
        st.subheader("Playwright Browser Collector")
        st.caption(
            "Opens real browser windows, scrolls through job listing pages, and "
            "collects offer URLs from public pages — then optionally imports them into your offers database."
        )

        from cv_sender.config import load_settings, save_settings  # noqa: PLC0415, F811

        settings_pw = load_settings()
        pw_cfg = settings_pw.playwright_collection

        # ---- Source selection ----
        st.markdown("**Sources**")
        pw_source_cols = st.columns(5)
        _pw_sources = ["justjoin", "rocketjobs", "nofluffjobs", "pracuj", "linkedin"]
        pw_src_enabled: dict[str, bool] = {}
        for _i, _name in enumerate(_pw_sources):
            with pw_source_cols[_i]:
                pw_src_enabled[_name] = st.checkbox(
                    _name,
                    value=_name != "linkedin",
                    key=f"pw_src_{_name}",
                )

        # ---- Config ----
        col_pw1, col_pw2, col_pw3 = st.columns(3)
        with col_pw1:
            pw_headless = st.checkbox(
                "Headless mode",
                value=pw_cfg.headless,
                help="Run browser without visible window (may be blocked by some sites)",
            )
            pw_max_scrolls = st.number_input(
                "Max scrolls per page",
                min_value=1,
                max_value=30,
                value=pw_cfg.max_scrolls_per_source,
            )
        with col_pw2:
            pw_max_urls = st.number_input(
                "Max URLs per source",
                min_value=1,
                max_value=200,
                value=pw_cfg.max_urls_per_source,
            )
            pw_slow_mo = st.number_input(
                "Slow-mo delay (ms)",
                min_value=0,
                max_value=2000,
                value=pw_cfg.slow_mo_ms,
                step=50,
            )
        with col_pw3:
            pw_scroll_pause = st.number_input(
                "Scroll pause (ms)",
                min_value=200,
                max_value=5000,
                value=pw_cfg.scroll_pause_ms,
                step=100,
            )
            pw_save_screenshots = st.checkbox(
                "Save debug screenshots",
                value=pw_cfg.save_debug_screenshots,
            )

        # ---- Custom listing URLs ----
        with st.expander("Custom listing URLs (optional — overrides auto-generated URLs)"):
            pw_custom_justjoin = st.text_area(
                "JustJoin.it URLs (one per line)", height=70, key="pw_custom_justjoin"
            )
            pw_custom_rocketjobs = st.text_area(
                "RocketJobs URLs (one per line)", height=70, key="pw_custom_rocketjobs"
            )
            pw_custom_nofluffjobs = st.text_area(
                "NoFluffJobs URLs (one per line)", height=70, key="pw_custom_nofluffjobs"
            )
            pw_custom_pracuj = st.text_area(
                "Pracuj.pl URLs (one per line)", height=70, key="pw_custom_pracuj"
            )

        def _pw_parse_urls(text: str) -> list[str]:
            return [u.strip() for u in text.splitlines() if u.strip().startswith("http")]

        pw_custom_map: dict[str, list[str]] = {}
        for _src_name, _custom_text in [
            ("justjoin", pw_custom_justjoin),
            ("rocketjobs", pw_custom_rocketjobs),
            ("nofluffjobs", pw_custom_nofluffjobs),
            ("pracuj", pw_custom_pracuj),
        ]:
            parsed_urls = _pw_parse_urls(_custom_text)
            if parsed_urls:
                pw_custom_map[_src_name] = parsed_urls

        # ---- Actions ----
        pw_collect_only = st.checkbox(
            "Collect URLs only",
            value=False,
            key="pw_collect_urls_only",
            help="Collect listing-page URLs without importing them yet.",
        )
        pw_do_import = st.checkbox(
            "Import after collection",
            value=True,
            key="pw_do_import",
            disabled=pw_collect_only,
        )
        pw_do_score = st.checkbox(
            "Score after import",
            value=True,
            key="pw_do_score",
            disabled=pw_collect_only or not pw_do_import,
        )
        pw_add_to_queue = st.checkbox(
            "Add imported offers to queue",
            value=False,
            key="pw_add_to_queue",
            disabled=pw_collect_only or not pw_do_import,
        )

        col_pw_save, col_pw_collect_only, col_pw_import_now, col_pw_collect_import = st.columns(4)

        with col_pw_save:
            if st.button("Save Playwright settings"):
                from cv_sender.config import PlaywrightCollectionConfig  # noqa: PLC0415

                new_pw_cfg = PlaywrightCollectionConfig(
                    enabled=True,
                    headless=pw_headless,
                    slow_mo_ms=int(pw_slow_mo),
                    max_scrolls_per_source=int(pw_max_scrolls),
                    scroll_pause_ms=int(pw_scroll_pause),
                    max_urls_per_source=int(pw_max_urls),
                    save_debug_screenshots=pw_save_screenshots,
                    page_timeout_ms=pw_cfg.page_timeout_ms,
                )
                new_settings_pw = settings_pw.model_copy(update={"playwright_collection": new_pw_cfg})
                save_settings(new_settings_pw)
                st.success("Playwright settings saved.")

        with col_pw_collect_only:
            if st.button("Collect URLs with Playwright", key="pw_collect_only_button"):
                from cv_sender.collectors.base import JobSearchCriteria  # noqa: PLC0415, F811
                from cv_sender.config import PlaywrightCollectionConfig  # noqa: PLC0415
                from cv_sender.playwright_collection import collect_job_urls  # noqa: PLC0415

                run_criteria = JobSearchCriteria.from_config(settings_pw.job_search)
                run_cfg = PlaywrightCollectionConfig(
                    enabled=True,
                    headless=pw_headless,
                    slow_mo_ms=int(pw_slow_mo),
                    max_scrolls_per_source=int(pw_max_scrolls),
                    scroll_pause_ms=int(pw_scroll_pause),
                    max_urls_per_source=int(pw_max_urls),
                    save_debug_screenshots=pw_save_screenshots,
                    page_timeout_ms=pw_cfg.page_timeout_ms,
                )
                active_pw_sources = [n for n, v in pw_src_enabled.items() if v]

                with st.spinner(f"Playwright collecting URLs from: {', '.join(active_pw_sources)} …"):
                    only_results = collect_job_urls(
                        criteria=run_criteria,
                        sources=active_pw_sources,
                        cfg=run_cfg,
                        custom_listing_urls=pw_custom_map or None,
                    )

                collected_urls_only = [cu.url for r in only_results for cu in r.collected_urls]
                st.session_state["pw_last_result"] = {
                    "collection_results": only_results,
                    "total_collected": len(collected_urls_only),
                    "total_imported": 0,
                    "total_duplicates": sum(r.duplicate_count for r in only_results),
                    "total_failed": 0,
                    "import_result": None,
                    "errors": [e for r in only_results for e in r.errors],
                }
                st.success(f"Collected {len(collected_urls_only)} URLs (not yet imported).")

        with col_pw_import_now:
            if st.button("Import collected URLs", key="pw_import_now_top"):
                from cv_sender.collectors.base import JobSearchCriteria  # noqa: PLC0415
                from cv_sender.playwright_collection import import_collected_urls  # noqa: PLC0415

                pw_last_result = st.session_state.get("pw_last_result") or {}
                prior_results = pw_last_result.get("collection_results", [])
                if not prior_results:
                    st.warning("No collected URLs available yet. Run Playwright collection first.")
                else:
                    run_criteria = JobSearchCriteria.from_config(settings_pw.job_search)
                    imported_total = 0
                    duplicate_total = 0
                    failed_total = 0
                    with st.spinner("Importing collected URLs …"):
                        for r in prior_results:
                            summary = import_collected_urls(
                                r,
                                auto_score=pw_do_score,
                                criteria=run_criteria,
                                add_to_queue=pw_add_to_queue,
                                attach_to_active_campaigns=False,
                            )
                            imported_total += summary.imported_count
                            duplicate_total += summary.duplicate_count
                            failed_total += summary.failed_count
                    pw_last_result["total_imported"] = imported_total
                    pw_last_result["total_duplicates"] = duplicate_total + pw_last_result.get("total_duplicates", 0)
                    pw_last_result["total_failed"] = failed_total
                    pw_last_result["import_result"] = None
                    st.session_state["pw_last_result"] = pw_last_result
                    st.success(
                        f"Imported: {imported_total} · Duplicates: {duplicate_total} · Failed: {failed_total}"
                    )

        with col_pw_collect_import:
            if st.button("Collect + Import + Score", type="primary", key="pw_collect"):
                from cv_sender.apply_queue import build_apply_queue_from_offers  # noqa: PLC0415
                from cv_sender.campaigns import build_campaign_queue, get_active_campaigns  # noqa: PLC0415
                from cv_sender.collectors.base import JobSearchCriteria  # noqa: PLC0415, F811
                from cv_sender.config import PlaywrightCollectionConfig  # noqa: PLC0415
                from cv_sender.playwright_collection import collect_and_import  # noqa: PLC0415

                run_criteria = JobSearchCriteria.from_config(settings_pw.job_search)
                run_cfg = PlaywrightCollectionConfig(
                    enabled=True,
                    headless=pw_headless,
                    slow_mo_ms=int(pw_slow_mo),
                    max_scrolls_per_source=int(pw_max_scrolls),
                    scroll_pause_ms=int(pw_scroll_pause),
                    max_urls_per_source=int(pw_max_urls),
                    save_debug_screenshots=pw_save_screenshots,
                    page_timeout_ms=pw_cfg.page_timeout_ms,
                )
                active_pw_sources = [n for n, v in pw_src_enabled.items() if v]

                with st.spinner(f"Playwright collecting from: {', '.join(active_pw_sources)} …"):
                    pw_result = collect_and_import(
                        criteria=run_criteria,
                        sources=active_pw_sources,
                        cfg=run_cfg,
                        auto_score=pw_do_score if pw_do_import else False,
                        custom_listing_urls=pw_custom_map or None,
                    )
                    if pw_add_to_queue and pw_result["total_imported"]:
                        build_apply_queue_from_offers()
                        for campaign in get_active_campaigns():
                            build_campaign_queue(campaign.id)

                st.session_state["pw_last_result"] = pw_result

                st.success(
                    f"Done! Collected: **{pw_result['total_collected']}** · "
                    f"Imported: **{pw_result['total_imported']}** · "
                    f"Duplicates: {pw_result['total_duplicates']} · "
                    f"Failed: {pw_result['total_failed']}"
                )

        st.markdown("---")
        st.subheader("Playwright Source Debugger")

        col_dbg1, col_dbg2, col_dbg3 = st.columns(3)
        with col_dbg1:
            dbg_source = st.selectbox(
                "Source",
                ["rocketjobs", "justjoin", "pracuj", "nofluffjobs"],
                index=0,
                key="pw_dbg_source",
            )
            dbg_keyword = st.text_input("Keyword", value="React", key="pw_dbg_keyword")
        with col_dbg2:
            dbg_listing_url = st.text_input("Custom listing URL (optional)", value="", key="pw_dbg_listing_url")
            dbg_headless = st.checkbox("Headless", value=False, key="pw_dbg_headless")
        with col_dbg3:
            dbg_max_scrolls = st.number_input("Max scrolls", min_value=1, max_value=30, value=5, key="pw_dbg_max_scrolls")
            dbg_save_html = st.checkbox("Save HTML", value=False, key="pw_dbg_save_html")
            dbg_save_screenshot = st.checkbox("Save screenshot", value=True, key="pw_dbg_save_screenshot")
            dbg_save_trace = st.checkbox("Save trace", value=False, key="pw_dbg_save_trace")

        if st.button("Debug selected source", key="pw_run_debug", type="secondary"):
            from cv_sender.collectors.base import JobSearchCriteria  # noqa: PLC0415
            from cv_sender.playwright_collection import debug_collect_source  # noqa: PLC0415

            run_criteria = JobSearchCriteria.from_config(settings_pw.job_search)
            run_criteria.keywords = [dbg_keyword] if dbg_keyword else run_criteria.keywords

            with st.spinner(f"Running debugger for {dbg_source} …"):
                dbg_report = debug_collect_source(
                    source=dbg_source,
                    criteria=run_criteria,
                    listing_url=dbg_listing_url or None,
                    headless=bool(dbg_headless),
                    max_scrolls=int(dbg_max_scrolls),
                    save_html=bool(dbg_save_html),
                    save_screenshot=bool(dbg_save_screenshot),
                    save_trace=bool(dbg_save_trace),
                )
            st.session_state["pw_debug_report"] = dbg_report
            st.success(f"Debug run complete. Files saved to: {dbg_report.debug_dir}")

        pw_dbg = st.session_state.get("pw_debug_report")
        if pw_dbg is not None:
            st.caption(
                f"Debug run {pw_dbg.run_id[:8]}… · source={pw_dbg.source} · status={pw_dbg.status}"
            )
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Raw links", pw_dbg.summary_counts.get("raw_links_found", 0))
            m2.metric("Job offers", pw_dbg.summary_counts.get("job_offer", 0))
            m3.metric("Listings", pw_dbg.summary_counts.get("listing", 0))
            m4.metric("Needs review", pw_dbg.summary_counts.get("needs_review", 0))

            st.write(f"Debug files path: {pw_dbg.debug_dir}")
            st.write(f"Cookie banner handled: {'yes' if getattr(pw_dbg, 'cookie_banner_handled', False) else 'no'}")
            st.info(f"Suggested next fix: {pw_dbg.suggested_next_fix}")

            modal_actions = list(getattr(pw_dbg, "modal_actions", []))
            modal_warnings = list(getattr(pw_dbg, "modal_warnings", []))
            if modal_actions:
                with st.expander(f"Modal actions ({len(modal_actions)})", expanded=False):
                    for act in modal_actions:
                        st.write(f"- {act}")
            if modal_warnings:
                with st.expander(f"Modal warnings ({len(modal_warnings)})", expanded=False):
                    for warn in modal_warnings:
                        st.warning(warn)

            from pathlib import Path  # noqa: PLC0415
            import json  # noqa: PLC0415
            import pandas as pd  # noqa: PLC0415

            dbg_dir = Path(pw_dbg.debug_dir)
            screenshot_after = dbg_dir / "screenshot_after_scroll.png"
            screenshot_initial = dbg_dir / "screenshot_initial.png"
            if screenshot_after.exists():
                st.image(str(screenshot_after), caption="After scroll")
            elif screenshot_initial.exists():
                st.image(str(screenshot_initial), caption="Initial screenshot")

            classified_path = dbg_dir / "classified_links.json"
            if classified_path.exists():
                classified_rows = json.loads(classified_path.read_text(encoding="utf-8"))
                if classified_rows:
                    with st.expander(f"Classified links ({len(classified_rows)})", expanded=False):
                        st.dataframe(pd.DataFrame(classified_rows), use_container_width=True)

            cards_path = dbg_dir / "job_card_candidates.json"
            if cards_path.exists():
                card_rows = json.loads(cards_path.read_text(encoding="utf-8"))
                if card_rows:
                    with st.expander(f"Job card candidates ({len(card_rows)})", expanded=False):
                        st.dataframe(pd.DataFrame(card_rows), use_container_width=True)

        # ---- Results panel ----
        pw_last = st.session_state.get("pw_last_result")
        if pw_last:
            st.subheader("Last Playwright collection")
            col_res1, col_res2, col_res3, col_res4 = st.columns(4)
            col_res1.metric("Collected URLs", pw_last["total_collected"])
            col_res2.metric("Imported", pw_last["total_imported"])
            col_res3.metric("Duplicates", pw_last["total_duplicates"])
            col_res4.metric("Failed", pw_last["total_failed"])

            if pw_last.get("errors"):
                with st.expander(f"Errors ({len(pw_last['errors'])})", expanded=True):
                    for err in pw_last["errors"]:
                        st.error(err)

            col_results = pw_last.get("collection_results", [])
            if col_results:
                import pandas as pd  # noqa: PLC0415

                rows = [
                    {
                        "Source": r.source,
                        "Listing URLs": len(r.listing_urls),
                        "Raw links": r.raw_link_count,
                        "Job URLs": r.job_url_count,
                        "Relevant": sum(1 for cu in r.collected_urls if getattr(cu, "relevance_decision", "") == "relevant"),
                        "Needs review": sum(1 for cu in r.collected_urls if getattr(cu, "relevance_decision", "") == "needs_review")
                        + len(getattr(r, "needs_review_urls", [])),
                        "Irrelevant": sum(1 for cu in r.collected_urls if getattr(cu, "relevance_decision", "") == "irrelevant"),
                        "Rejected listing": len(getattr(r, "collected_listing_urls", [])),
                        "Rejected company": len(getattr(r, "company_urls", [])),
                        "Rejected nav": len(getattr(r, "navigation_urls", [])),
                        "Unknown": len(getattr(r, "unknown_urls", [])),
                        "Duplicates": r.duplicate_count,
                        "Errors": len(r.errors),
                        "Warnings": len(r.warnings),
                    }
                    for r in col_results
                ]
                st.dataframe(pd.DataFrame(rows), use_container_width=True)

                detailed_rows = []
                for r in col_results:
                    for item in r.collected_urls:
                        detailed_rows.append(
                            {
                                "URL": item.url,
                                "Source": item.source,
                                "Classification": getattr(item, "classification_type", "job_offer"),
                                "Classification reason": getattr(item, "classification_reason", ""),
                                "Relevance score": getattr(item, "relevance_score", 0),
                                "Matched keywords": ", ".join(getattr(item, "matched_keywords", [])),
                                "Matched technologies": ", ".join(getattr(item, "matched_technologies", [])),
                                "Negative matches": ", ".join(getattr(item, "rejected_keywords", [])),
                                "Relevance decision": getattr(item, "relevance_decision", "irrelevant"),
                                "Action": getattr(item, "suggested_action", "ignore"),
                            }
                        )

                    rejected_groups = [
                        getattr(r, "collected_listing_urls", []),
                        getattr(r, "company_urls", []),
                        getattr(r, "navigation_urls", []),
                        getattr(r, "unknown_urls", []),
                        getattr(r, "needs_review_urls", []),
                    ]
                    for group in rejected_groups:
                        for item in group:
                            detailed_rows.append(
                                {
                                    "URL": item.url,
                                    "Source": item.source,
                                    "Classification": getattr(item, "classification_type", "unknown"),
                                    "Classification reason": getattr(item, "classification_reason", ""),
                                    "Relevance score": getattr(item, "relevance_score", 0),
                                    "Matched keywords": ", ".join(getattr(item, "matched_keywords", [])),
                                    "Matched technologies": ", ".join(getattr(item, "matched_technologies", [])),
                                    "Negative matches": ", ".join(getattr(item, "rejected_keywords", [])),
                                    "Relevance decision": getattr(item, "relevance_decision", "needs_review"),
                                    "Action": getattr(item, "suggested_action", "ignore"),
                                }
                            )

                if detailed_rows:
                    with st.expander(f"Per-URL diagnostics ({len(detailed_rows)})"):
                        st.dataframe(pd.DataFrame(detailed_rows), use_container_width=True)

                for r in col_results:
                    if r.warnings:
                        with st.expander(f"{r.source} warnings"):
                            for w in r.warnings:
                                st.warning(w)

                show_rejected = st.checkbox(
                    "Show rejected/listing links",
                    value=False,
                    key="pw_show_rejected_links",
                )
                if show_rejected:
                    ignored_urls = st.session_state.setdefault("pw_ignored_needs_review", set())
                    for r in col_results:
                        buckets = [
                            ("listing", getattr(r, "collected_listing_urls", [])),
                            ("company", getattr(r, "company_urls", [])),
                            ("navigation", getattr(r, "navigation_urls", [])),
                            ("unknown", getattr(r, "unknown_urls", [])),
                        ]
                        total_rejected = sum(len(items) for _, items in buckets)
                        if total_rejected == 0 and not getattr(r, "needs_review_urls", []):
                            continue

                        with st.expander(f"{r.source} rejected URLs ({total_rejected})"):
                            for bucket_name, items in buckets:
                                if not items:
                                    continue
                                st.caption(f"{bucket_name}: {len(items)}")
                                preview = [item.url for item in items[:20]]
                                st.text_area(
                                    f"{r.source} {bucket_name} URLs",
                                    value="\n".join(preview),
                                    height=120,
                                    key=f"pw_rejected_{r.source}_{bucket_name}",
                                )

                            review_items = [
                                item for item in getattr(r, "needs_review_urls", [])
                                if item.url not in ignored_urls
                            ]
                            if review_items:
                                st.caption(f"needs_review: {len(review_items)}")
                                for idx, item in enumerate(review_items[:20]):
                                    c_url, c_import, c_ignore = st.columns([6, 1, 1])
                                    c_url.write(item.url)
                                    if c_import.button("Import anyway", key=f"pw_import_review_{r.source}_{idx}"):
                                        from cv_sender.apply_queue import build_apply_queue_from_offers  # noqa: PLC0415
                                        from cv_sender.campaigns import build_campaign_queue, get_active_campaigns  # noqa: PLC0415
                                        from cv_sender.services import import_offer_from_url  # noqa: PLC0415

                                        with st.spinner("Importing reviewed URL …"):
                                            item_result = import_offer_from_url(item.url, auto_score=pw_do_score)
                                            if pw_add_to_queue and item_result.status.value == "imported":
                                                build_apply_queue_from_offers()
                                                for campaign in get_active_campaigns():
                                                    build_campaign_queue(campaign.id)
                                        if item_result.error:
                                            st.error(f"{item.url}: {item_result.error}")
                                        else:
                                            st.success(f"Imported reviewed URL: {item.url}")
                                    if c_ignore.button("Ignore", key=f"pw_ignore_review_{r.source}_{idx}"):
                                        ignored_urls.add(item.url)
                                        st.session_state["pw_ignored_needs_review"] = ignored_urls
                                        st.rerun()

            # Show collected URLs if collect-only mode
            all_collected = [
                cu.url for r in col_results for cu in r.collected_urls
            ]
            if all_collected and pw_last["total_imported"] == 0:
                with st.expander(f"Collected URLs ({len(all_collected)} — not yet imported)"):
                    urls_text = "\n".join(all_collected)
                    st.text_area("URLs", value=urls_text, height=200, key="pw_urls_preview")
                    if st.button("Import these URLs now", key="pw_import_now"):
                        from cv_sender.collectors.base import JobSearchCriteria  # noqa: PLC0415
                        from cv_sender.playwright_collection import import_collected_urls  # noqa: PLC0415

                        run_criteria = JobSearchCriteria.from_config(settings_pw.job_search)
                        imported_total = 0
                        duplicate_total = 0
                        failed_total = 0
                        with st.spinner("Importing …"):
                            for r in col_results:
                                summary = import_collected_urls(
                                    r,
                                    auto_score=pw_do_score,
                                    criteria=run_criteria,
                                    add_to_queue=pw_add_to_queue,
                                    attach_to_active_campaigns=False,
                                )
                                imported_total += summary.imported_count
                                duplicate_total += summary.duplicate_count
                                failed_total += summary.failed_count
                        st.success(
                            f"Imported: {imported_total} · "
                            f"Duplicates: {duplicate_total} · "
                            f"Failed: {failed_total}"
                        )
                        pw_last["total_imported"] = imported_total
                        pw_last["import_result"] = None
                        st.rerun()

    # ================================================================== #
    # Tab 3 — Rapid apply queue
    # ================================================================== #
    with tab_queue:
        st.subheader("Rapid Apply Queue")
        col_rebuild, col_stats = st.columns([2, 3])
        with col_rebuild:
            if st.button("Rebuild queue from offers"):
                with st.spinner("Building queue …"):
                    build_apply_queue_from_offers()
                st.success("Queue rebuilt.")
                st.rerun()

        queue = load_apply_queue()
        stats = get_queue_stats()
        with col_stats:
            stat_cols = st.columns(len(ApplyQueueItemStatus))
            for i, status in enumerate(ApplyQueueItemStatus):
                with stat_cols[i]:
                    st.metric(status.value, stats.get(status, 0))

        if not queue:
            st.info("Queue is empty. Click **Rebuild queue** or collect offers first.")
            return

        import pandas as pd  # noqa: PLC0415

        from cv_sender.listing import ListQuery, build_list_result, init_list_state, render_pagination_controls  # noqa: PLC0415

        active_statuses = {ApplyQueueItemStatus.QUEUED, ApplyQueueItemStatus.IN_PROGRESS}
        active_items = [q for q in queue if q.status in active_statuses]

        if not active_items:
            st.info("No active items in the queue. All done!")
        else:
            _CQ_PFX = "jobsearch_queue"
            init_list_state(_CQ_PFX, {"page_size": 25, "sort_by": "priority_score", "sort_dir": "desc"})

            # Filter controls
            _cq_sources = sorted({i.source for i in active_items if i.source})
            cq_col1, cq_col2, cq_col3 = st.columns(3)
            _cq_filter_source: str = cq_col1.selectbox(
                "Source filter", ["(all)"] + _cq_sources, key=f"{_CQ_PFX}_filter_source"
            )
            _cq_min_priority: float = cq_col2.number_input(
                "Min priority score", min_value=0.0, max_value=200.0, value=0.0, step=5.0,
                key=f"{_CQ_PFX}_filter_min_priority"
            )
            _cq_sort_opts = ["priority_score", "score", "company", "source"]
            _cq_sort_by: str = cq_col3.selectbox(
                "Sort by", _cq_sort_opts, key=f"{_CQ_PFX}_sort_by"
            )

            # Reset page on filter change
            _cq_watched = (_cq_filter_source, _cq_min_priority, _cq_sort_by)
            if st.session_state.get(f"{_CQ_PFX}_filter_sentinel") != _cq_watched:
                st.session_state[f"{_CQ_PFX}_page"] = 1
            st.session_state[f"{_CQ_PFX}_filter_sentinel"] = _cq_watched

            def _cq_filter_fn(item) -> bool:  # noqa: ANN001
                if _cq_filter_source != "(all)" and (item.source or "") != _cq_filter_source:
                    return False
                if _cq_min_priority > 0 and (item.priority_score or 0) < _cq_min_priority:
                    return False
                return True

            _cq_query = ListQuery(
                page=st.session_state.get(f"{_CQ_PFX}_page", 1),
                page_size=st.session_state.get(f"{_CQ_PFX}_page_size", 25),
                sort_by=_cq_sort_by,
                sort_dir="desc",
            )
            _cq_result = build_list_result(active_items, _cq_query, filter_fn=_cq_filter_fn)

            st.markdown(f"**{_cq_result.total_count} active** items in queue (sorted by {_cq_sort_by})")
            _cq_new_page = render_pagination_controls(_CQ_PFX, _cq_result)
            if _cq_new_page != st.session_state.get(f"{_CQ_PFX}_page"):
                st.session_state[f"{_CQ_PFX}_page"] = _cq_new_page
                st.rerun()

            for item in _cq_result.items:
                with st.expander(
                    f"[{item.priority_score:.0f}] {item.title} @ {item.company} — {item.source}"
                ):
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.markdown(f"**Score:** {item.score or 'N/A'}")
                        st.markdown(f"**Priority:** {item.priority_score:.1f}")
                        if item.url:
                            st.markdown(f"[Open offer]({item.url})")
                    with col2:
                        if item.reasons:
                            st.markdown("**Reasons:** " + ", ".join(item.reasons[:3]))
                        if item.warnings:
                            st.warning("Warnings: " + "; ".join(item.warnings[:3]))
                    with col3:
                        btn_fill = st.button("Fill form", key=f"fill_{item.id}")
                        btn_send = st.button("Mark sent", key=f"sent_{item.id}")
                        btn_skip = st.button("Skip", key=f"skip_{item.id}")

                    if btn_fill:
                        mark_queue_item_status(item.id, ApplyQueueItemStatus.IN_PROGRESS)
                        st.info("Marked as In Progress. Open the offer URL and use the Bookmarklet to fill.")
                        st.rerun()
                    if btn_send:
                        mark_queue_item_status(item.id, ApplyQueueItemStatus.SENT)
                        st.success("Marked as Sent.")
                        st.rerun()
                    if btn_skip:
                        mark_queue_item_status(item.id, ApplyQueueItemStatus.SKIPPED)
                        st.rerun()

        with st.expander("Show completed / skipped items"):
            done_items = [q for q in queue if q.status not in active_statuses]
            if done_items:
                rows = [
                    {
                        "Status": q.status,
                        "Title": q.title,
                        "Company": q.company,
                        "Source": q.source,
                        "Score": q.score or "",
                        "Priority": f"{q.priority_score:.1f}",
                    }
                    for q in done_items
                ]
                st.dataframe(pd.DataFrame(rows), use_container_width=True)
            else:
                st.info("No completed items yet.")


def _page_campaigns() -> None:  # noqa: PLR0912, PLR0914, PLR0915
    """Apply Campaign mode — create focused application sprints."""
    st.title("Apply Campaigns")

    from datetime import date as _date  # noqa: PLC0415

    from cv_sender.campaigns import (  # noqa: PLC0415
        REACT_SPRINT_PRESET,
        build_campaign_queue,
        complete_campaign_if_target_reached,
        create_campaign,
        generate_campaign_summary,
        get_active_campaigns,
        get_campaign,
        get_campaign_progress,
        mark_campaign_sent,
        record_campaign_activity,
        update_campaign_status,
    )
    from cv_sender.models import (  # noqa: PLC0415
        CampaignActivityType,
        CampaignGoalType,
        CampaignStatus,
    )
    from cv_sender.storage import (  # noqa: PLC0415
        get_campaign_by_id,
        load_apply_queue,
        load_campaigns,
        update_campaign,
    )

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------
    tab_create, tab_active, tab_queue, tab_summary, tab_diag = st.tabs(
        ["Create Campaign", "Active Campaigns", "Campaign Queue", "Session Summary", "Collection Diagnostics"]
    )

    # ==================================================================
    # TAB 1 — Create campaign
    # ==================================================================
    with tab_create:
        st.subheader("New Campaign")

        # Preset loader
        preset_col, _ = st.columns([1, 3])
        with preset_col:
            if st.button("React Emergency Sprint", type="primary"):
                for k, v in REACT_SPRINT_PRESET.items():
                    st.session_state[f"nc_{k}"] = v
                st.success("Preset loaded — review and click Create.")

        st.divider()

        # Defaults (populated by preset or user typing)
        def _sv(key: str, default):  # noqa: ANN001
            return st.session_state.get(f"nc_{key}", default)

        with st.form("create_campaign_form"):
            name = st.text_input("Campaign name", value=_sv("name", ""))
            c1, c2, c3 = st.columns(3)
            with c1:
                target_count = st.number_input(
                    "Target (applications)", min_value=1, max_value=500,
                    value=_sv("target_count", 25), step=1,
                )
            with c2:
                goal_type_choice = st.selectbox(
                    "Goal type",
                    [g.value for g in CampaignGoalType],
                    index=[g.value for g in CampaignGoalType].index(
                        _sv("goal_type", CampaignGoalType.APPLICATIONS_SENT)
                    ),
                )
            with c3:
                target_date = st.date_input(
                    "Target date",
                    value=_sv("target_date", _date.today().isoformat()),
                )

            st.markdown("**Search criteria**")
            kw_col, tech_col, loc_col = st.columns(3)
            with kw_col:
                keywords_raw = st.text_area(
                    "Keywords (one per line)",
                    value="\n".join(_sv("keywords", [])),
                    height=100,
                )
            with tech_col:
                technologies_raw = st.text_area(
                    "Technologies (one per line)",
                    value="\n".join(_sv("technologies", [])),
                    height=100,
                )
            with loc_col:
                locations_raw = st.text_area(
                    "Locations (one per line)",
                    value="\n".join(_sv("locations", [])),
                    height=100,
                )

            src_all = ["justjoin", "rocketjobs", "nofluffjobs", "pracuj", "linkedin"]
            preset_sources = _sv("sources", [])
            sources_chosen = st.multiselect(
                "Sources", src_all,
                default=[s for s in preset_sources if s in src_all],
            )

            campaign_mode = st.selectbox(
                "Collector mode override (optional)",
                options=["", "playwright", "api_static", "hybrid"],
                format_func=lambda x: "Use global setting" if x == "" else x,
            )

            opt_col1, opt_col2, opt_col3, opt_col4 = st.columns(4)
            with opt_col1:
                min_score = st.number_input(
                    "Min score", min_value=0, max_value=100,
                    value=_sv("min_score", 0), step=5,
                )
            with opt_col2:
                min_salary = st.number_input(
                    "Min salary B2B", min_value=0,
                    value=_sv("min_salary_b2b", 0), step=1000,
                )
            with opt_col3:
                require_salary = st.checkbox(
                    "Require salary",
                    value=_sv("require_salary", False),
                )
            with opt_col4:
                include_follow_ups = st.checkbox(
                    "Include follow-ups",
                    value=_sv("include_follow_ups", False),
                )

            notes = st.text_area("Notes (optional)", value="", height=60)

            submitted = st.form_submit_button("Create Campaign", type="primary")

        if submitted:
            if not name.strip():
                st.error("Campaign name is required.")
            else:
                campaign = create_campaign(
                    name=name.strip(),
                    target_count=int(target_count),
                    target_date=str(target_date),
                    goal_type=CampaignGoalType(goal_type_choice),
                    keywords=[k.strip() for k in keywords_raw.splitlines() if k.strip()],
                    technologies=[t.strip() for t in technologies_raw.splitlines() if t.strip()],
                    locations=[lo.strip() for lo in locations_raw.splitlines() if lo.strip()],
                    sources=sources_chosen,
                    collector_mode=campaign_mode,
                    min_score=int(min_score),
                    min_salary_b2b=int(min_salary),
                    require_salary=require_salary,
                    include_follow_ups=include_follow_ups,
                    notes=notes,
                )
                st.success(f"Campaign **{campaign.name}** created (id: `{campaign.id}`)")
                # Offer to immediately attach matching queue items
                attached = build_campaign_queue(campaign.id)
                if attached:
                    st.info(f"Attached {len(attached)} matching queue item(s) to the campaign.")
                st.rerun()

    # ==================================================================
    # TAB 2 — Active campaigns dashboard
    # ==================================================================
    with tab_active:
        active = get_active_campaigns()
        all_campaigns = load_campaigns()

        if not all_campaigns:
            st.info("No campaigns yet. Create one in the **Create Campaign** tab.")
        else:
            # Paused / archived in an expander
            non_active = [c for c in all_campaigns if c.status != CampaignStatus.ACTIVE]

            for campaign in active:
                progress = get_campaign_progress(campaign.id)
                with st.container(border=True):
                    h1, h2 = st.columns([4, 1])
                    with h1:
                        st.markdown(f"### {campaign.name}")
                        st.caption(
                            f"Target: {campaign.target_count} by {campaign.target_date} · "
                            f"Goal: {campaign.goal_type}"
                        )
                    with h2:
                        st.markdown(
                            f"<div style='text-align:right;font-size:1.6rem;font-weight:bold'>"
                            f"{progress.progress_pct if progress else 0:.0f}%</div>",
                            unsafe_allow_html=True,
                        )

                    if progress:
                        prog_cols = st.columns(6)
                        prog_cols[0].metric("Sent", progress.sent)
                        prog_cols[1].metric("Remaining", progress.remaining)
                        prog_cols[2].metric("Queued", progress.queued_available)
                        prog_cols[3].metric("Filled", progress.filled_not_sent)
                        prog_cols[4].metric("Skipped", progress.skipped)
                        prog_cols[5].metric("Failed", progress.failed)

                        st.progress(min(1.0, progress.progress_pct / 100))

                        if progress.queue_shortage and progress.remaining > 0:
                            st.warning(f"⚠️ {progress.queue_shortage_message}")
                        elif progress.remaining == 0:
                            st.success("Target reached!")

                    action_col1, action_col2, action_col3, action_col4, action_col5 = st.columns(5)
                    with action_col1:
                        if st.button("Collect more offers", key=f"collect_{campaign.id}"):
                            from cv_sender.collectors.base import JobSearchCriteria  # noqa: PLC0415
                            from cv_sender.campaigns import resolve_campaign_collector_mode  # noqa: PLC0415
                            from cv_sender.config import load_settings  # noqa: PLC0415
                            from cv_sender.job_search import collect_jobs  # noqa: PLC0415

                            settings_campaign = load_settings()
                            mode = resolve_campaign_collector_mode(campaign, settings_campaign.job_search.collector_mode)

                            criteria = JobSearchCriteria(
                                keywords=campaign.keywords or ["React Developer"],
                                technologies=campaign.technologies or ["React"],
                                locations=campaign.locations or ["Remote"],
                                min_salary_b2b=campaign.min_salary_b2b,
                                require_salary=campaign.require_salary,
                            )
                            sources = campaign.sources or ["justjoin", "rocketjobs", "nofluffjobs", "pracuj"]
                            with st.spinner("Collecting offers …"):
                                report = collect_jobs(
                                    criteria,
                                    mode=mode,
                                    source_names=sources,
                                    auto_score=True,
                                )
                            st.success(
                                f"Collection done (mode={mode}). Imported: {report.total_accepted}, failed: {report.total_failed}."
                            )
                            st.rerun()
                    with action_col2:
                        if st.button("Build campaign queue from search", key=f"buildq_{campaign.id}"):
                            from cv_sender.apply_queue import build_apply_queue_from_offers  # noqa: PLC0415
                            from cv_sender.collectors.base import JobSearchCriteria  # noqa: PLC0415
                            from cv_sender.campaigns import resolve_campaign_collector_mode  # noqa: PLC0415
                            from cv_sender.config import load_settings  # noqa: PLC0415
                            from cv_sender.job_search import collect_jobs  # noqa: PLC0415

                            settings_campaign = load_settings()
                            mode = resolve_campaign_collector_mode(campaign, settings_campaign.job_search.collector_mode)
                            criteria = JobSearchCriteria(
                                keywords=campaign.keywords or ["React Developer"],
                                technologies=campaign.technologies or ["React"],
                                locations=campaign.locations or ["Remote"],
                                min_salary_b2b=campaign.min_salary_b2b,
                                require_salary=campaign.require_salary,
                            )
                            sources = campaign.sources or ["justjoin", "rocketjobs", "nofluffjobs", "pracuj"]

                            with st.spinner("Building queue …"):
                                collect_jobs(
                                    criteria,
                                    mode=mode,
                                    source_names=sources,
                                    auto_score=True,
                                )
                                build_apply_queue_from_offers()
                                attached = build_campaign_queue(
                                    campaign.id,
                                    min_score=campaign.min_score or None,
                                )
                            st.success(f"Queue rebuilt. {len(attached)} item(s) attached.")
                            st.rerun()
                    with action_col3:
                        if st.button("Start Rapid Apply", key=f"ra_{campaign.id}", type="primary"):
                            st.session_state.ra_campaign_id = campaign.id
                            st.session_state.ra_item_id = None
                            st.session_state.ra_fill_result = None
                            st.session_state.ra_quality = None
                            st.session_state.ra_processed = 0
                            st.info("Navigate to **Rapid Apply** page to start the session.")
                    with action_col4:
                        if st.button("Mark complete", key=f"complete_{campaign.id}"):
                            update_campaign_status(campaign.id, CampaignStatus.COMPLETED)
                            st.success("Campaign marked complete.")
                            st.rerun()
                    with action_col5:
                        if st.button("Pause", key=f"pause_{campaign.id}"):
                            update_campaign_status(campaign.id, CampaignStatus.PAUSED)
                            st.info("Campaign paused.")
                            st.rerun()

            if non_active:
                with st.expander(f"Other campaigns ({len(non_active)})"):
                    for campaign in non_active:
                        c1, c2, c3 = st.columns([3, 1, 1])
                        with c1:
                            st.markdown(f"**{campaign.name}** — {campaign.status}")
                        with c2:
                            if campaign.status == CampaignStatus.PAUSED:
                                if st.button("Resume", key=f"resume_{campaign.id}"):
                                    update_campaign_status(campaign.id, CampaignStatus.ACTIVE)
                                    st.rerun()
                        with c3:
                            if campaign.status != CampaignStatus.ARCHIVED:
                                if st.button("Archive", key=f"archive_{campaign.id}"):
                                    update_campaign_status(campaign.id, CampaignStatus.ARCHIVED)
                                    st.rerun()

    # ==================================================================
    # TAB 3 — Campaign queue
    # ==================================================================
    with tab_queue:
        all_campaigns = load_campaigns()
        if not all_campaigns:
            st.info("No campaigns yet.")
        else:
            camp_names = {c.id: c.name for c in all_campaigns}
            selected_camp_id = st.selectbox(
                "Select campaign",
                options=list(camp_names.keys()),
                format_func=lambda cid: camp_names[cid],
                key="cq_selected_campaign",
            )

            if selected_camp_id:
                all_queue = load_apply_queue()
                camp_items = [q for q in all_queue if q.campaign_id == selected_camp_id]

                if not camp_items:
                    st.info("No queue items attached to this campaign. Use **Build/rebuild queue** from the Active Campaigns tab.")
                else:
                    from cv_sender.listing import ListQuery as _LQcq, build_list_result as _blrcq, init_list_state as _ilscq, render_pagination_controls as _rpccq  # noqa: PLC0415, E501
                    _CAMPQ_PFX = f"campq_{selected_camp_id[:8]}"
                    _ilscq(_CAMPQ_PFX, {"page_size": 25, "sort_by": "priority_score", "sort_dir": "desc"})

                    _campq_q = _LQcq(
                        page=st.session_state.get(f"{_CAMPQ_PFX}_page", 1),
                        page_size=25,
                        sort_by="priority_score",
                        sort_dir="desc",
                    )
                    _campq_result = _blrcq(camp_items, _campq_q)
                    st.caption(f"{_campq_result.total_count} item(s) in campaign queue")
                    _campq_new_page = _rpccq(_CAMPQ_PFX, _campq_result)
                    if _campq_new_page != st.session_state.get(f"{_CAMPQ_PFX}_page"):
                        st.session_state[f"{_CAMPQ_PFX}_page"] = _campq_new_page
                        st.rerun()

                    for item in _campq_result.items:
                        status_icon = {
                            "queued": "🟡", "in_progress": "🔵", "filled": "🟢",
                            "failed": "🔴", "sent": "✅", "skipped": "⏭️",
                        }.get(item.status, "⚪")
                        with st.container(border=True):
                            row1, row2 = st.columns([4, 1])
                            with row1:
                                st.markdown(
                                    f"{status_icon} **{item.company}** — {item.title} "
                                    f"({item.source}) · Score {item.score or '?'} · "
                                    f"Priority {item.priority_score:.1f}"
                                )
                                if item.warnings:
                                    st.caption("⚠️ " + " · ".join(item.warnings[:2]))
                            with row2:
                                if st.button("Remove from campaign", key=f"rem_{item.id}"):
                                    from cv_sender.storage import update_queue_item  # noqa: PLC0415

                                    updated = item.model_copy(update={"campaign_id": ""})
                                    update_queue_item(updated)
                                    st.rerun()

    # ==================================================================
    # TAB 4 — Session summary
    # ==================================================================
    with tab_summary:
        all_campaigns = load_campaigns()
        if not all_campaigns:
            st.info("No campaigns yet.")
        else:
            camp_names = {c.id: c.name for c in all_campaigns}
            summary_camp_id = st.selectbox(
                "Select campaign",
                options=list(camp_names.keys()),
                format_func=lambda cid: camp_names[cid],
                key="cs_selected_campaign",
            )

            if summary_camp_id:
                summary = generate_campaign_summary(summary_camp_id)
                st.markdown(summary)

                progress = get_campaign_progress(summary_camp_id)
                if progress:
                    st.divider()
                    st.markdown("**Next recommended action:**")
                    if progress.remaining == 0:
                        st.success("Target reached! Consider marking the campaign complete.")
                    elif progress.filled_not_sent > 0:
                        st.info(f"You have {progress.filled_not_sent} filled form(s). "
                                "Open the browser and submit them, then click Mark as sent.")
                    elif progress.queue_shortage:
                        st.warning(
                            f"Queue has only {progress.queued_available} offer(s) left. "
                            "Collect more offers first."
                        )
                    else:
                        st.info(
                            f"You sent {progress.sent}/{progress.target} applications. "
                            f"{progress.remaining} remaining. Start Rapid Apply to continue."
                        )

    # ==================================================================
    # TAB 5 — Collection diagnostics
    # ==================================================================
    with tab_diag:
        from cv_sender.collector_diagnostics import (  # noqa: PLC0415
            get_latest_collection_diagnostics,
            force_import_collected_offer,
        )

        report = get_latest_collection_diagnostics()
        if report is None:
            st.info("No collection diagnostics yet. Run a collection from Job Search or from the Active Campaigns tab.")
        else:
            st.caption(f"Run {report.run_id[:8]}… · {report.started_at.strftime('%Y-%m-%d %H:%M')} · "
                       f"Found: {report.total_found} · Imported: {report.total_accepted} · "
                       f"Rejected: {report.total_rejected} · Duplicates: {report.total_duplicates}")

            # Suggestions / queue shortage reasons
            if report.suggestions:
                st.subheader("Suggestions")
                for s in report.suggestions:
                    st.info(f"💡 {s}")

            # Per-source status
            if report.source_summaries:
                st.subheader("Per-source status")
                import pandas as pd  # noqa: PLC0415
                src_rows = [
                    {
                        "Source": ss.source,
                        "Collector": ss.collector_used,
                        "Status": ss.status,
                        "Raw": ss.raw_found_count,
                        "Job URLs": ss.job_offer_url_count or ss.found_count,
                        "Found": ss.found_count,
                        "Imported": ss.imported_count or ss.accepted_count,
                        "Skipped": ss.skipped_count or ss.rejected_count,
                        "Duplicates": ss.duplicate_count,
                        "Failed": ss.failed_count,
                        "Error": ss.error or "",
                    }
                    for ss in report.source_summaries
                ]
                st.dataframe(pd.DataFrame(src_rows), use_container_width=True)

            # Rejected offers
            rejected = [d for d in report.decisions if d.decision in ("rejected", "needs_review", "failed")]
            if rejected:
                with st.expander(f"Rejected / skipped offers ({len(rejected)})", expanded=False):
                    from cv_sender.listing import ListQuery as _LQ2, build_list_result as _blr2, init_list_state as _ils2, render_pagination_controls as _rpc2  # noqa: PLC0415, E501
                    _REJ2_PFX = "camp_diag_rejected"
                    _ils2(_REJ2_PFX, {"page_size": 25})
                    _rej2_sources = sorted({d.source for d in rejected if d.source})
                    _rej2_c1, _rej2_c2 = st.columns(2)
                    _rej2_filter_src: str = _rej2_c1.selectbox(
                        "Source", ["(all)"] + _rej2_sources, key=f"{_REJ2_PFX}_filter_src"
                    )
                    _rej2_filter_dec: str = _rej2_c2.selectbox(
                        "Decision", ["(all)", "rejected", "needs_review", "failed"],
                        key=f"{_REJ2_PFX}_filter_dec"
                    )
                    _rej2_watched = (_rej2_filter_src, _rej2_filter_dec)
                    if st.session_state.get(f"{_REJ2_PFX}_sentinel") != _rej2_watched:
                        st.session_state[f"{_REJ2_PFX}_page"] = 1
                    st.session_state[f"{_REJ2_PFX}_sentinel"] = _rej2_watched

                    def _rej2_fn(d) -> bool:  # noqa: ANN001
                        if _rej2_filter_src != "(all)" and (d.source or "") != _rej2_filter_src:
                            return False
                        if _rej2_filter_dec != "(all)" and d.decision != _rej2_filter_dec:
                            return False
                        return True

                    _rej2_q = _LQ2(page=st.session_state.get(f"{_REJ2_PFX}_page", 1), page_size=25)
                    _rej2_result = _blr2(rejected, _rej2_q, filter_fn=_rej2_fn)
                    _rej2_new_page = _rpc2(_REJ2_PFX, _rej2_result)
                    if _rej2_new_page != st.session_state.get(f"{_REJ2_PFX}_page"):
                        st.session_state[f"{_REJ2_PFX}_page"] = _rej2_new_page
                        st.rerun()

                    for d in _rej2_result.items:
                        with st.container(border=True):
                            c1, c2, c3 = st.columns([3, 2, 1])
                            with c1:
                                st.markdown(f"**{d.title}** — {d.company} ({d.source})")
                                if d.url:
                                    st.link_button("Open", d.url)
                            with c2:
                                st.markdown(f"Decision: **{d.decision}**")
                                if d.reasons:
                                    st.caption("Reasons: " + ", ".join(d.reasons))
                                st.caption(f"Salary: {d.salary_status}")
                                if d.error:
                                    st.caption(f"Error: {d.error}")
                            with c3:
                                if st.button("Import anyway", key=f"cfi_{d.id}"):
                                    ok, msg = force_import_collected_offer(d, auto_score=True)
                                    if ok:
                                        st.success(msg)
                                    else:
                                        st.error(msg)


def _page_bookmarklet() -> None:
    from cv_sender.bookmarklet_server import BOOKMARKLET_JS

    st.title("Bookmarklet – Save to Job Assistant")

    st.markdown(
        """
        The **Save to Job Assistant** bookmarklet lets you import any job offer page
        directly from your browser by clicking a single bookmark.

        > ⚠️ This does **not** crawl or scrape the page content.  
        > The offer is stored with a title derived from the URL path.
        > Fill in company, salary, and description manually via the Offers page.
        """
    )

    st.markdown("---")
    st.subheader("1 · Start the bookmarklet server")
    st.markdown(
        "Run this command in a **separate terminal** while you browse:"
    )
    st.code("cv-sender bookmarklet-server", language="bash")
    st.caption(
        "The server listens on `http://127.0.0.1:8765` and is only reachable"
        " from this machine."
    )

    st.markdown("---")
    st.subheader("2 · Create the browser bookmark")
    st.markdown(
        """
        1. Open your browser's bookmarks bar.  
        2. Create a new bookmark (right-click the bookmarks bar → *Add page…*).  
        3. Set the **name** to: `Save to Job Assistant`  
        4. Set the **URL / address** to the JavaScript code below (copy the entire line):
        """
    )
    st.code(BOOKMARKLET_JS, language="javascript")

    st.markdown("---")
    st.subheader("3 · Use it")
    st.markdown(
        """
        1. Make sure the **bookmarklet server** is running (`cv-sender bookmarklet-server`).  
        2. Make sure the **Streamlit UI** is running (`cv-sender ui`).  
        3. Open any job offer page in your browser.  
        4. Click the **Save to Job Assistant** bookmark.  
        5. A new tab opens showing the import result (imported / duplicate / error).  
        6. Come back to this UI → **Offers** to review, score, and manage the imported offer.
        """
    )

    st.markdown("---")
    st.subheader("Local endpoints")
    c1, c2 = st.columns(2)
    c1.markdown("**Bookmarklet receiver**")
    c1.code("http://127.0.0.1:8765/import?url=<encoded-url>")
    c2.markdown("**Health check**")
    c2.code("http://127.0.0.1:8765/health")

    st.markdown("---")
    st.info(
        "🔒 **Security**: the bookmarklet server binds to `127.0.0.1` only. "
        "It is not reachable from other machines on your network."
    )


# ---------------------------------------------------------------------------
# Debug page
# ---------------------------------------------------------------------------


def _load_json_file(path: Path) -> Any:
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _render_playwright_debug_file_preview(path: Path, label: str) -> None:
    if not path.exists():
        st.warning(f"Missing file: {path.name}")
        return

    suffix = path.suffix.lower()
    if suffix == ".png":
        st.image(str(path), caption=label)
        return

    if suffix == ".json":
        payload = _load_json_file(path)
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            import pandas as pd

            st.dataframe(pd.DataFrame(payload), use_container_width=True)
            return
        st.json(payload)
        return

    text = path.read_text(encoding="utf-8")
    if suffix == ".md":
        st.markdown(text)
        return
    st.code(text)


def _render_playwright_debug_rerun_buttons(run: Any, settings: Settings, key_prefix: str) -> None:
    from cv_sender.collectors.base import JobSearchCriteria  # noqa: PLC0415
    from cv_sender.playwright_collection import debug_collect_source  # noqa: PLC0415

    def _rerun(*, headless: bool | None = None, cookie_mode: str | None = None) -> None:
        criteria = JobSearchCriteria.from_config(settings.job_search)
        keyword = (getattr(run, "keyword", "") or getattr(run, "query", "") or "").strip()
        if keyword:
            criteria.keywords = [keyword]

        override = {"cookie_mode": cookie_mode} if cookie_mode else None
        effective_headless = getattr(run, "headless", None)
        if headless is not None:
            effective_headless = headless
        if effective_headless is None:
            effective_headless = False

        with st.spinner(f"Running Playwright debug for {run.source} …"):
            report = debug_collect_source(
                source=run.source,
                criteria=criteria,
                listing_url=getattr(run, "listing_url", "") or None,
                headless=bool(effective_headless),
                max_scrolls=5,
                save_html=False,
                save_screenshot=True,
                save_trace=False,
                modal_settings_override=override,
            )

        st.session_state["pw_debug_report"] = report
        st.session_state["pw_collector_debug_selected"] = f"{report.run_id}:{report.source}"
        st.success(f"Debug run complete. Files saved to: {report.debug_dir}")
        st.rerun()

    col1, col2, col3, col4 = st.columns(4)
    if col1.button("Rerun debug for this source", key=f"{key_prefix}_rerun_default"):
        _rerun()
    if col2.button("Rerun with headless=false", key=f"{key_prefix}_rerun_headed"):
        _rerun(headless=False)
    if col3.button("Rerun with accept_all cookies", key=f"{key_prefix}_rerun_accept"):
        _rerun(cookie_mode="accept_all")
    if col4.button("Rerun with reject_optional cookies", key=f"{key_prefix}_rerun_reject"):
        _rerun(cookie_mode="reject_optional")


def _render_playwright_debug_details(run: Any, settings: Settings) -> None:
    metadata = _load_json_file(Path(run.files.get("metadata.json", ""))) if run.files.get("metadata.json") else {}
    modal_actions = _load_json_file(Path(run.files.get("modal_actions.json", ""))) if run.files.get("modal_actions.json") else []
    classified_links = _load_json_file(Path(run.files.get("classified_links.json", ""))) if run.files.get("classified_links.json") else []
    job_cards = _load_json_file(Path(run.files.get("job_card_candidates.json", ""))) if run.files.get("job_card_candidates.json") else []
    raw_links = _load_json_file(Path(run.files.get("links.json", ""))) if run.files.get("links.json") else []
    modal_summary = dict((metadata or {}).get("modal_summary") or {}) if isinstance(metadata, dict) else {}
    login_detection = dict((metadata or {}).get("login_detection") or {}) if isinstance(metadata, dict) else {}

    st.subheader("Selected Playwright collector run")
    st.caption(f"{run.run_id} · {run.source} · {run.status}")
    _render_playwright_debug_rerun_buttons(run, settings, f"detail_{run.run_id}_{run.source}")

    tab_summary, tab_screens, tab_modals, tab_classified, tab_cards, tab_raw, tab_report, tab_files = st.tabs(
        [
            "Summary",
            "Screenshots",
            "Cookie/modal actions",
            "Classified links",
            "Job card candidates",
            "Raw links",
            "Report markdown",
            "Files",
        ]
    )

    with tab_summary:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Raw links", run.raw_links_count)
        c2.metric("Job offers", run.job_offer_count)
        c3.metric("Listings", run.listing_count)
        c4.metric("Needs review", run.needs_review_count)
        st.write(f"Debug folder: {run.debug_dir}")
        st.write(f"Listing URL: {run.listing_url or '—'}")
        st.write(f"Final URL: {run.final_url or '—'}")
        st.write(f"Page title: {run.page_title or '—'}")
        st.write(f"Keyword/query: {run.query or run.keyword or '—'}")
        st.write(
            "Flags: "
            f"handler_called={'yes' if run.handler_called else 'no'} · "
            f"cookie_before={'yes' if run.cookie_banner_visible_before else 'no'} · "
            f"cookie_after={'yes' if run.cookie_banner_visible_after else 'no'} · "
            f"captcha={'yes' if run.captcha_detected else 'no'} · "
            f"login={'yes' if run.login_detected else 'no'} · "
            f"blocked={'yes' if run.blocked_detected else 'no'}"
        )
        if login_detection:
            st.markdown("**Login detection**")
            st.write(
                " · ".join(
                    [
                        f"Navigation login link: {'yes' if login_detection.get('navigation_login_link_detected') else 'no'}",
                        f"Login redirect: {'yes' if login_detection.get('login_redirect_detected') else 'no'}",
                        f"Login form: {'yes' if login_detection.get('login_form_detected') else 'no'}",
                        f"Useful content: {'yes' if login_detection.get('useful_content_detected') else 'no'}",
                        f"Login wall: {'yes' if login_detection.get('login_wall_detected') else 'no'}",
                    ]
                )
            )
            if login_detection.get("reason"):
                st.caption(f"Reason: {login_detection.get('reason')}")
            if login_detection.get("navigation_login_link_detected") and not login_detection.get("login_wall_detected"):
                st.info("Login link was found in navigation, but the page was not blocked.")
        if modal_summary:
            st.json(modal_summary)
        for warning in getattr(run, "warnings", []):
            st.warning(warning)

    with tab_screens:
        for label, name in (
            ("Initial screenshot", "screenshot_initial.png"),
            ("After modals", "screenshot_after_modals.png"),
            ("After scroll", "screenshot_after_scroll.png"),
        ):
            file_path = Path(run.files[name]) if name in run.files else None
            if file_path and file_path.exists():
                st.image(str(file_path), caption=label)
            else:
                st.info(f"{label} not available.")

    with tab_modals:
        if isinstance(modal_actions, list) and modal_actions:
            import pandas as pd

            st.dataframe(pd.DataFrame(modal_actions), use_container_width=True)
        else:
            st.info("No modal actions recorded. This may mean the modal handler was not called.")
        if modal_summary:
            st.json(modal_summary)

    with tab_classified:
        if isinstance(classified_links, list) and classified_links:
            import pandas as pd

            st.dataframe(pd.DataFrame(classified_links), use_container_width=True)
        else:
            st.info("classified_links.json not available for this run.")

    with tab_cards:
        if isinstance(job_cards, list) and job_cards:
            import pandas as pd

            st.dataframe(pd.DataFrame(job_cards), use_container_width=True)
        else:
            st.info("job_card_candidates.json not available for this run.")

    with tab_raw:
        if isinstance(raw_links, dict):
            st.json(raw_links)
        elif isinstance(raw_links, list) and raw_links:
            if isinstance(raw_links[0], dict):
                import pandas as pd

                st.dataframe(pd.DataFrame(raw_links), use_container_width=True)
            else:
                st.json(raw_links)
        else:
            st.info("links.json not available for this run.")

    with tab_report:
        report_path = Path(run.files["debug_report.md"]) if "debug_report.md" in run.files else None
        if report_path and report_path.exists():
            st.markdown(report_path.read_text(encoding="utf-8"))
        else:
            st.info("debug_report.md not available for this run.")

    with tab_files:
        for name, file_path in sorted(run.files.items()):
            st.write(f"{name}: {file_path}")
            with st.expander(f"Preview {name}", expanded=False):
                _render_playwright_debug_file_preview(Path(file_path), name)


def _render_playwright_debug_runs(settings: Settings) -> None:
    from cv_sender.playwright_debugger import discover_playwright_debug_runs  # noqa: PLC0415

    runs = discover_playwright_debug_runs(limit=10)
    st.subheader("Playwright Collector Debug")
    st.caption("Latest runs discovered under data/debug/playwright_collectors/.")
    if not runs:
        st.info("No Playwright collector debug runs found.")
        return

    selected_run_key = st.session_state.get("pw_collector_debug_selected")
    if not selected_run_key:
        selected_run_key = f"{runs[0].run_id}:{runs[0].source}"
        st.session_state["pw_collector_debug_selected"] = selected_run_key

    preview_state = st.session_state.get("pw_collector_debug_preview")

    for run in runs:
        run_key = f"{run.run_id}:{run.source}"
        st.markdown("---")
        st.markdown(f"**{run.source}** · {run.run_id[:8]}… · {run.status}")

        top1, top2 = st.columns([3, 2])
        with top1:
            st.write(f"Started: {run.started_at.strftime('%Y-%m-%d %H:%M:%S') if run.started_at else '—'}")
            st.write(f"Keyword/query: {run.query or run.keyword or '—'}")
            st.write(f"Listing URL: {run.listing_url or '—'}")
            st.write(f"Final URL: {run.final_url or '—'}")
            st.write(f"Page title: {run.page_title or '—'}")
        with top2:
            st.write(f"Debug folder: {run.debug_dir}")
            st.write(
                "Flags: "
                f"modal_actions={run.modal_actions_count} · "
                f"captcha={'yes' if run.captcha_detected else 'no'} · "
                f"login={'yes' if run.login_detected else 'no'} · "
                f"nav_login={'yes' if getattr(run, 'navigation_login_link_detected', False) else 'no'} · "
                f"blocked={'yes' if run.blocked_detected else 'no'}"
            )
            if getattr(run, "navigation_login_link_detected", False) and not getattr(run, "login_detected", False):
                st.info("Login link was found in navigation, but the page was not blocked.")
            st.write(
                "Cookie visibility: "
                f"before={'yes' if run.cookie_banner_visible_before else 'no'} · "
                f"after={'yes' if run.cookie_banner_visible_after else 'no'} · "
                f"handler_called={'yes' if run.handler_called else 'no'}"
            )

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Raw links", run.raw_links_count)
        m2.metric("Job offers", run.job_offer_count)
        m3.metric("Listings", run.listing_count)
        m4.metric("Needs review", run.needs_review_count)
        m5.metric("Unknown", run.unknown_count)

        if run.metadata_missing:
            st.warning(f"metadata.json missing for {run.run_id}/{run.source}")
        for warning in run.warnings:
            st.warning(warning)

        action_cols = st.columns(7)
        if action_cols[0].button("Open details", key=f"open_details_{run_key}"):
            st.session_state["pw_collector_debug_selected"] = run_key
        for idx, (label, file_name) in enumerate(
            [
                ("Open screenshot initial", "screenshot_initial.png"),
                ("Open screenshot after scroll", "screenshot_after_scroll.png"),
                ("Open debug_report.md", "debug_report.md"),
                ("Open links.json", "links.json"),
                ("Open classified_links.json", "classified_links.json"),
                ("Open modal_actions.json", "modal_actions.json"),
            ],
            start=1,
        ):
            disabled = file_name not in run.files
            if action_cols[idx].button(label, key=f"preview_{file_name}_{run_key}", disabled=disabled):
                st.session_state["pw_collector_debug_preview"] = {"run_key": run_key, "file_name": file_name}

        _render_playwright_debug_rerun_buttons(run, settings, f"card_{run.run_id}_{run.source}")

        if preview_state and preview_state.get("run_key") == run_key:
            preview_file = preview_state.get("file_name", "")
            preview_path = Path(run.files.get(preview_file, "")) if preview_file in run.files else None
            with st.expander(f"Preview: {preview_file}", expanded=True):
                if preview_path is not None:
                    _render_playwright_debug_file_preview(preview_path, preview_file)
                else:
                    st.warning("Selected file is no longer available.")

    selected_run = next((run for run in runs if f"{run.run_id}:{run.source}" == st.session_state.get("pw_collector_debug_selected")), runs[0])
    st.markdown("---")
    _render_playwright_debug_details(selected_run, settings)


def _page_debug() -> None:
    st.title("Debug")
    st.caption("Inspect stored form-filling and Playwright collector debug runs.")

    from cv_sender.listing import ListQuery as _LQd, build_list_result as _blrd, init_list_state as _ilsd  # noqa: PLC0415, E501

    _DBG_PFX = "debug_runs"
    _ilsd(_DBG_PFX, {"page_size": 10, "sort_by": "started_at", "sort_dir": "desc"})

    _dbg_page_size: int = st.selectbox(
        "Form-filling runs per page", [10, 25],
        index=[10, 25].index(st.session_state.get(f"{_DBG_PFX}_page_size", 10)),
        key=f"{_DBG_PFX}_page_size",
    )

    runs = services.get_debug_runs(limit=200)
    st.subheader("Form filling debug")
    st.caption("Last 200 form-filling debug runs stored under data/debug/form_filling/.")
    if not runs:
        st.info("No form-filling debug runs found. Enable form_filling.debug in settings to capture them.")
    else:
        import pandas as pd

        _dbg_q = _LQd(
            page=st.session_state.get(f"{_DBG_PFX}_page", 1),
            page_size=_dbg_page_size,
            sort_by="started_at",
            sort_dir="desc",
        )
        _dbg_result = _blrd(runs, _dbg_q)

        # Summary dataframe (all runs, small)
        summary = pd.DataFrame(
            [
                {
                    "Started": r.started_at.strftime("%Y-%m-%d %H:%M"),
                    "Source": r.source,
                    "Filler": r.filler_name,
                    "Status": r.status,
                    "Fields filled": len(r.fields_filled),
                    "Fields missing": len(r.fields_missing),
                    "Warnings": len(r.warnings),
                    "Error": (r.error or "")[:60],
                    "Run ID": r.run_id[:8] + "…",
                    "_run_id": r.run_id,
                }
                for r in _dbg_result.items
            ]
        )
        st.caption(
            f"Showing **{_dbg_result.start_index}–{_dbg_result.end_index}** "
            f"of **{_dbg_result.total_count}** runs  "
            f"(page {_dbg_result.page}/{_dbg_result.total_pages})"
        )
        _dbg_prev_col, _dbg_next_col, _ = st.columns([1, 1, 4])
        if _dbg_prev_col.button("◀ Prev", key=f"{_DBG_PFX}_prev", disabled=not _dbg_result.has_prev):
            st.session_state[f"{_DBG_PFX}_page"] = _dbg_result.page - 1
            st.rerun()
        if _dbg_next_col.button("Next ▶", key=f"{_DBG_PFX}_next", disabled=not _dbg_result.has_next):
            st.session_state[f"{_DBG_PFX}_page"] = _dbg_result.page + 1
            st.rerun()

        st.dataframe(summary.drop(columns=["_run_id"]), use_container_width=True)

        st.markdown("---")
        st.subheader("Inspect a form-filling run")
        run_options = {r.run_id[:8] + "… " + r.started_at.strftime("%Y-%m-%d %H:%M") + f" [{r.source}]": r.run_id for r in runs}
        selected_label = st.selectbox("Select run", list(run_options.keys()))
        if selected_label:
            selected_run_id = run_options[selected_label]
            run = services.get_debug_run(selected_run_id)
            if run:
                c1, c2, c3 = st.columns(3)
                c1.metric("Status", run.status)
                c2.metric("Fields filled", len(run.fields_filled))
                c3.metric("Fields missing", len(run.fields_missing))

                st.markdown(f"**URL:** {run.url or '—'}")
                st.markdown(f"**Offer ID:** `{run.offer_id or '—'}`")
                st.markdown(f"**Filler:** {run.filler_name or '—'}")
                st.markdown(f"**Run ID:** `{run.run_id}`")

                if run.fields_filled:
                    st.success("Filled: " + ", ".join(run.fields_filled))
                if run.fields_missing:
                    st.warning("Missing: " + ", ".join(run.fields_missing))
                for w in run.warnings:
                    st.warning(w)
                if run.error:
                    st.error(run.error)
                if run.fields_detected_summary:
                    st.json(run.fields_detected_summary)

                _render_debug_artifacts(run.run_id, run.screenshot_path)

    st.markdown("---")
    _render_playwright_debug_runs(load_settings())


# ---------------------------------------------------------------------------
# Data Cleanup page
# ---------------------------------------------------------------------------


def _page_data_cleanup() -> None:  # noqa: PLR0912, PLR0915
    """Bulk delete and cleanup tools for offers and related data."""
    import pandas as pd  # noqa: PLC0415

    from cv_sender.cleanup import (  # noqa: PLC0415
        BulkDeleteResult,
        OfferDeleteFilters,
        RelatedCleanupOptions,
        clear_apply_queue,
        clear_collection_diagnostics,
        clear_debug_data,
        delete_all_offers,
        delete_offers,
        delete_offers_by_filter,
        dev_cleanup,
        preview_offers_by_filter,
    )

    st.title("🗑️ Data Cleanup")
    st.caption(
        "Bulk delete offers and related data.  "
        "A backup is created automatically before every destructive action."
    )

    offers = _safe_load_offers()

    # --- shared option widgets -------------------------------------------------

    def _options_form(key_prefix: str) -> RelatedCleanupOptions:
        col1, col2, col3, col4 = st.columns(4)
        q = col1.checkbox("Delete related queue items", value=True, key=f"{key_prefix}_queue")
        qr = col2.checkbox("Delete related quality reports", value=True, key=f"{key_prefix}_qr")
        apps = col3.checkbox(
            "Delete related applications",
            value=False,
            key=f"{key_prefix}_apps",
            help="⚠️ This will delete sent application history. Off by default.",
        )
        dbg = col4.checkbox("Delete debug runs", value=False, key=f"{key_prefix}_debug")
        return RelatedCleanupOptions(
            delete_queue_items=q,
            delete_quality_reports=qr,
            delete_applications=apps,
            delete_debug_runs=dbg,
        )

    def _backup_checkbox(key: str) -> bool:
        return st.checkbox("Create backup before deleting", value=True, key=key)

    def _show_bulk_result(result: BulkDeleteResult) -> None:
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Deleted", result.deleted_count)
        col2.metric("Not found", result.not_found_count)
        col3.metric("Failed", result.failed_count)
        col4.metric("Requested", result.requested_count)
        if result.backup_path:
            st.info(f"Backup saved to: `{result.backup_path}`")
        for err in result.errors:
            st.error(err)

    # ==========================================================================
    # Section 1 — Bulk delete selected offers
    # ==========================================================================
    st.header("1 · Delete selected offers")

    if not offers:
        st.info("No offers in storage.")
    else:
        df_rows = [
            {
                "id": o.id,
                "company": o.company,
                "title": o.title,
                "source": o.source,
                "decision": str(o.decision or ""),
                "score": o.score,
                "created_at": o.created_at.strftime("%Y-%m-%d") if o.created_at else "",
                "url": o.url,
            }
            for o in offers
        ]
        df = pd.DataFrame(df_rows)
        edited = st.data_editor(
            df,
            column_config={
                "id": st.column_config.TextColumn("ID", width="small"),
                "company": st.column_config.TextColumn("Company"),
                "title": st.column_config.TextColumn("Title"),
                "source": st.column_config.TextColumn("Source", width="small"),
                "decision": st.column_config.TextColumn("Decision", width="small"),
                "score": st.column_config.NumberColumn("Score", width="small"),
                "created_at": st.column_config.TextColumn("Created", width="small"),
                "url": st.column_config.LinkColumn("URL"),
            },
            use_container_width=True,
            hide_index=False,
            num_rows="dynamic",
            key="cleanup_offer_table",
        )

        selected_indices: list[int] = []
        table_state = st.session_state.get("cleanup_offer_table", {})
        edited_rows = table_state.get("edited_rows", {})
        deleted_rows = table_state.get("deleted_rows", [])
        # Rows deleted via the data_editor trash icon are in deleted_rows
        selected_indices = list(deleted_rows)

        sel_ids: list[str] = []
        if selected_indices:
            sel_ids = [df.iloc[i]["id"] for i in selected_indices if i < len(df)]

        if sel_ids:
            st.info(f"{len(sel_ids)} offer(s) selected via table row deletion.")
        else:
            st.caption("To select offers for deletion, use the trash icon on table rows.")

        bulk_opts = _options_form("sel")
        bulk_backup = _backup_checkbox("sel_backup")
        bulk_confirm = st.checkbox(
            "✅ I understand this will permanently delete the selected offers",
            key="sel_confirm",
        )

        if st.button("🗑️ Delete selected offers", disabled=not sel_ids or not bulk_confirm):
            with st.spinner("Deleting…"):
                res = delete_offers(
                    sel_ids,
                    options=bulk_opts,
                    create_backup=bulk_backup,
                    backup_reason="bulk_delete_selected",
                )
            st.success(f"Done. {res.deleted_count} offer(s) deleted.")
            _show_bulk_result(res)
            st.rerun()

    st.divider()

    # ==========================================================================
    # Section 2 — Delete by filter
    # ==========================================================================
    st.header("2 · Delete by filter")

    all_sources = sorted({o.source for o in offers if o.source})
    all_decisions = sorted({str(o.decision) for o in offers if o.decision})

    fc1, fc2, fc3 = st.columns(3)
    flt_source = fc1.selectbox("Source", [""] + all_sources, key="flt_source", index=0)
    flt_decision = fc2.selectbox("Decision", [""] + all_decisions, key="flt_decision", index=0)
    flt_text = fc3.text_input("Search text (title / company)", key="flt_text")

    fc4, fc5, fc6 = st.columns(3)
    flt_score = fc4.number_input(
        "Score below (0 = not used)", min_value=0, max_value=100, value=0, step=1, key="flt_score"
    )
    flt_dev = fc5.checkbox("Dev / test offers only", key="flt_dev")
    flt_before = fc6.date_input("Created before (optional)", value=None, key="flt_before")

    flt_filters = OfferDeleteFilters(
        source=flt_source,
        decision=flt_decision,
        search_text=flt_text,
        score_below=int(flt_score) if flt_score > 0 else None,
        dev_only=flt_dev,
        max_created_at=(
            datetime.combine(flt_before, datetime.min.time()).replace(tzinfo=UTC)
            if flt_before
            else None
        ),
    )

    if st.button("🔍 Preview matching offers", key="flt_preview"):
        matches = preview_offers_by_filter(flt_filters)
        if not matches:
            st.warning("No offers match these filters.")
        else:
            st.success(f"{len(matches)} offer(s) match the filter.")
            preview_df = pd.DataFrame(
                [
                    {
                        "id": m.get("id", "")[:8],
                        "company": m.get("company", ""),
                        "title": m.get("title", ""),
                        "source": m.get("source", ""),
                        "score": m.get("score"),
                        "decision": m.get("decision", ""),
                        "created_at": (m.get("created_at") or "")[:10],
                    }
                    for m in matches
                ]
            )
            st.dataframe(preview_df, use_container_width=True, hide_index=True)
            st.session_state["flt_preview_count"] = len(matches)

    flt_opts = _options_form("flt")
    flt_backup = _backup_checkbox("flt_backup")
    flt_confirm = st.checkbox(
        "✅ I understand this will permanently delete all matching offers",
        key="flt_confirm",
    )
    preview_count = st.session_state.get("flt_preview_count", 0)

    if st.button(
        f"🗑️ Delete matching offers ({preview_count} previewed)",
        disabled=preview_count == 0 or not flt_confirm,
        key="flt_delete",
    ):
        with st.spinner("Deleting…"):
            res = delete_offers_by_filter(flt_filters, options=flt_opts, create_backup=flt_backup)
        st.success(f"Done. {res.deleted_count} offer(s) deleted.")
        _show_bulk_result(res)
        st.session_state["flt_preview_count"] = 0
        st.rerun()

    st.divider()

    # ==========================================================================
    # Section 3 — Danger zone
    # ==========================================================================
    st.header("3 · Danger zone")

    with st.expander("⚠️ Expand danger zone", expanded=False):

        # -- Delete all offers -------------------------------------------------
        st.subheader("Delete ALL offers")
        st.warning(
            "This will delete every offer in storage.  "
            "A backup will be created first unless you uncheck it."
        )
        dz_opts = _options_form("dz")
        dz_backup = _backup_checkbox("dz_backup")
        dz_typed = st.text_input(
            'Type **DELETE OFFERS** to confirm', key="dz_typed", placeholder="DELETE OFFERS"
        )
        if st.button("🗑️ Delete ALL offers", disabled=dz_typed.strip() != "DELETE OFFERS"):
            with st.spinner("Deleting all offers…"):
                res = delete_all_offers(options=dz_opts, create_backup=dz_backup)
            st.success(f"Done. {res.deleted_count} offer(s) deleted.")
            _show_bulk_result(res)
            st.rerun()

        st.divider()

        # -- Clear apply queue -------------------------------------------------
        st.subheader("Clear apply queue")
        cq_backup = _backup_checkbox("cq_backup")
        cq_confirm = st.checkbox("✅ I understand this will clear the queue", key="cq_confirm")
        if st.button("🗑️ Clear apply queue", disabled=not cq_confirm):
            with st.spinner("Clearing…"):
                res = clear_apply_queue(create_backup=cq_backup)
            st.success(f"Done. {res.deleted_count} queue item(s) removed.")
            _show_bulk_result(res)

        st.divider()

        # -- Clear collection diagnostics --------------------------------------
        st.subheader("Clear collection diagnostics")
        cd_backup = _backup_checkbox("cd_backup")
        cd_confirm = st.checkbox(
            "✅ I understand this will clear diagnostics history", key="cd_confirm"
        )
        if st.button("🗑️ Clear collection diagnostics", disabled=not cd_confirm):
            with st.spinner("Clearing…"):
                res = clear_collection_diagnostics(create_backup=cd_backup)
            st.success(f"Done. {res.deleted_count} diagnostic run(s) removed.")
            _show_bulk_result(res)

        st.divider()

        # -- Clear debug data --------------------------------------------------
        st.subheader("Clear debug data")
        st.caption("Removes form-filling debug run directories from data/debug/.")
        dbg_confirm = st.checkbox(
            "✅ I understand this will remove debug artifacts", key="dbg_confirm"
        )
        if st.button("🗑️ Clear debug data", disabled=not dbg_confirm):
            with st.spinner("Clearing…"):
                res = clear_debug_data()
            st.success(f"Done. {res.deleted_count} debug run(s) removed.")
            _show_bulk_result(res)

        st.divider()

        # -- Full dev cleanup --------------------------------------------------
        st.subheader("Full dev cleanup")
        st.warning(
            "Deletes **dev/test offers**, clears the apply queue, clears "
            "collection diagnostics.  Applications are NOT deleted by default."
        )
        devclean_opts = _options_form("devclean")
        devclean_backup = _backup_checkbox("devclean_backup")
        devclean_typed = st.text_input(
            'Type **DEV CLEANUP** to confirm', key="devclean_typed", placeholder="DEV CLEANUP"
        )
        if st.button(
            "🧹 Full dev cleanup", disabled=devclean_typed.strip() != "DEV CLEANUP"
        ):
            with st.spinner("Running dev cleanup…"):
                results = dev_cleanup(options=devclean_opts, create_backup=devclean_backup)
            st.success("Dev cleanup complete.")
            for op_name, op_res in results.items():
                with st.expander(f"{op_name}: {op_res.deleted_count} deleted", expanded=False):
                    _show_bulk_result(op_res)
            st.rerun()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

if page == "Dashboard":
    _page_dashboard()
    _page_dashboard()
elif page == "Offers":
    _page_offers()
elif page == "Applications":
    _page_applications()
elif page == "Profile":
    _page_profile()
elif page == "Settings":
    _page_settings()
elif page == "Gmail":
    _page_gmail()
elif page == "Interviews":
    _page_interviews()
elif page == "Analytics":
    _page_analytics()
elif page == "Job Search":
    _page_job_search()
elif page == "Rapid Apply":
    _page_rapid_apply()
elif page == "Campaigns":
    _page_campaigns()
elif page == "Bookmarklet":
    _page_bookmarklet()
elif page == "Debug":
    _page_debug()
elif page == "Data Cleanup":
    _page_data_cleanup()
