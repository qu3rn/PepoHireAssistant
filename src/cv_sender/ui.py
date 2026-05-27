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

_PAGES = ["Dashboard", "Offers", "Applications", "Profile", "Settings", "Gmail", "Interviews", "Bookmarklet", "Debug"]

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

    # --- Filters ---
    with st.expander("Filters", expanded=True):
        fc1, fc2, fc3, fc4 = st.columns(4)
        decision_opts = ["(all)"] + [d.value for d in Decision]
        sel_decision = fc1.selectbox("Decision", decision_opts)
        sources = sorted({o.source for o in offers if o.source})
        source_opts = ["(all)"] + sources
        sel_source = fc2.selectbox("Source", source_opts)
        sel_min_score = fc3.number_input("Min score", min_value=0, max_value=100, value=0, step=5)
        locations = sorted({o.location for o in offers if o.location})
        location_opts = ["(all)"] + locations
        sel_location = fc4.selectbox("Location", location_opts)

    filtered = offers
    if sel_decision != "(all)":
        filtered = [o for o in filtered if str(o.decision) == sel_decision]
    if sel_source != "(all)":
        filtered = [o for o in filtered if o.source == sel_source]
    if sel_min_score > 0:
        filtered = [o for o in filtered if (o.score or 0) >= sel_min_score]
    if sel_location != "(all)":
        filtered = [o for o in filtered if o.location == sel_location]

    st.caption(f"Showing {len(filtered)} of {len(offers)} offers")

    for offer in filtered:
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

    status_values = [s.value for s in ApplicationStatus]

    # ── Filters ──────────────────────────────────────────────────────────────
    with st.expander("🔍 Filters", expanded=False):
        fc1, fc2, fc3 = st.columns(3)
        filter_status = fc1.selectbox(
            "Status", ["(all)"] + status_values, key="app_filter_status"
        )
        filter_due_only = fc2.checkbox("Due follow-ups only", key="app_filter_due")
        filter_company = fc3.text_input("Company contains", key="app_filter_company")

    now = datetime.now(UTC)
    from cv_sender.follow_up import is_follow_up_due  # noqa: PLC0415

    def _matches(a: Application) -> bool:
        if filter_status != "(all)" and a.status.value != filter_status:
            return False
        if filter_due_only and not is_follow_up_due(a, now):
            return False
        if filter_company and filter_company.lower() not in (a.company or "").lower():
            return False
        return True

    apps = [a for a in all_apps if _matches(a)]
    st.subheader(f"Showing {len(apps)} of {len(all_apps)} applications")

    for app in sorted(apps, key=lambda a: a.updated_at, reverse=True):
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
        status_filter = st.selectbox(
            "Filter by match status",
            ["All", "pending", "applied", "ignored"],
            key="gmail_status_filter",
        )
        shown = all_matches if status_filter == "All" else [m for m in all_matches if m.status == status_filter]
        shown = sorted(shown, key=lambda m: m.received_at, reverse=True)

        st.caption(f"Showing {len(shown)} of {len(all_matches)} matches")

        for match in shown:
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
# Bookmarklet page
# ---------------------------------------------------------------------------


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


def _page_debug() -> None:
    st.title("Debug – Form filling runs")
    st.caption(
        "Shows the last 50 form-filling debug runs. "
        "Enable `form_filling.debug: true` in settings to capture screenshots and form snapshots."
    )

    runs = services.get_debug_runs(limit=50)
    if not runs:
        st.info("No debug runs found. Debug artifacts are stored under `data/debug/form_filling/`.")
        return

    import pandas as pd

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
            for r in runs
        ]
    )
    st.dataframe(summary.drop(columns=["_run_id"]), use_container_width=True)

    st.markdown("---")
    st.subheader("Inspect a run")
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


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

if page == "Dashboard":
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
elif page == "Bookmarklet":
    _page_bookmarklet()
elif page == "Debug":
    _page_debug()
