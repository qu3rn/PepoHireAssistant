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
    ApplicationEvent,
    ApplicationStatus,
    Decision,
    Offer,
)
from cv_sender.storage import (  # noqa: E402
    add_application,
    load_applications,
    load_offers,
    save_applications,
    save_offers,
    update_application,
    update_offer,
)

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

_PAGES = ["Dashboard", "Offers", "Applications", "Profile", "Settings"]

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


def _rescore_offer(offer: Offer, settings: Settings) -> Offer:
    from cv_sender.scorer import score_offer

    return score_offer(offer, settings, llm_result=None)


# ---------------------------------------------------------------------------
# Dashboard page
# ---------------------------------------------------------------------------


def _page_dashboard() -> None:
    st.title("Dashboard")

    offers = _safe_load_offers()
    apps = _safe_load_applications()

    total_offers = len(offers)
    apply_count = sum(1 for o in offers if o.decision == Decision.APPLY)
    skipped_count = sum(1 for o in offers if o.decision == Decision.SKIP)

    total_apps = len(apps)
    sent = sum(1 for a in apps if a.status == ApplicationStatus.SENT)
    replies = sum(1 for a in apps if a.status == ApplicationStatus.REPLY_RECEIVED)
    interviews = sum(1 for a in apps if a.status == ApplicationStatus.INTERVIEW)
    rejected = sum(1 for a in apps if a.status == ApplicationStatus.REJECTED)
    got_offer = sum(1 for a in apps if a.status == ApplicationStatus.OFFER)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total offers", total_offers)
    col2.metric("Apply", apply_count)
    col3.metric("Skip", skipped_count)
    col4.metric("Applications sent", sent)

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("Replies received", replies)
    col6.metric("Interviews", interviews)
    col7.metric("Rejected", rejected)
    col8.metric("Offers received", got_offer)

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

    offers = _safe_load_offers()
    settings = load_settings()

    if not offers:
        st.info("No offers found. The file `data/offers.json` is empty or does not exist.")
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

            btn_col1, btn_col2, btn_col3, btn_col4 = st.columns(4)

            if btn_col1.button("Re-score", key=f"rescore_{offer.id}"):
                updated = _rescore_offer(offer, settings)
                update_offer(updated)
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
                _trigger_form_fill(offer)


def _trigger_form_fill(offer: Offer) -> None:
    """Call the existing form-filler and record the application."""
    try:
        from cv_sender.form_filler import fill_application

        profile = load_profile()
        settings = load_settings()
        fill_application(offer, profile, settings)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Browser error: {exc}")
        return

    # Record application
    application = Application(
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
        status=ApplicationStatus.READY_TO_SEND,
        score=offer.score,
        events=[
            ApplicationEvent(
                event="form_filled",
                details="Form filled via cv-sender UI; awaiting manual submission",
            )
        ],
    )
    add_application(application)

    st.success(
        "Application form has been filled. Please review it manually before submitting."
    )
    st.info(f"Application saved (id: {application.id[:8]})")


# ---------------------------------------------------------------------------
# Applications page
# ---------------------------------------------------------------------------


def _page_applications() -> None:
    st.title("Applications")

    apps = _safe_load_applications()

    if not apps:
        st.info("No applications yet. The file `data/applications.json` is empty or does not exist.")
        return

    import pandas as pd

    status_values = [s.value for s in ApplicationStatus]

    st.subheader(f"Total: {len(apps)}")

    for app in sorted(apps, key=lambda a: a.updated_at, reverse=True):
        label = f"**{app.title}** — {app.company}  |  {app.status}  |  {app.created_at.date()}"
        with st.expander(label):
            c1, c2, c3 = st.columns(3)
            c1.markdown(f"**Source:** {app.source or '—'}")
            c1.markdown(f"**Location:** {app.location or '—'}")
            c1.markdown(f"**Contract:** {app.contract or '—'}")
            c2.markdown(f"**Salary:** {_salary_str(app)}")
            c2.markdown(f"**Score:** {app.score or '—'}")
            c3.markdown(f"**Created:** {app.created_at.date()}")
            c3.markdown(f"**Updated:** {app.updated_at.date()}")
            if app.url:
                st.markdown(f"[Offer URL]({app.url})")

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
                changed = app.model_copy(
                    update={
                        "status": ApplicationStatus(new_status),
                        "notes": new_notes,
                        "updated_at": datetime.now(UTC),
                    }
                )
                if new_status != app.status.value:
                    changed.events.append(
                        ApplicationEvent(
                            event="status_changed",
                            details=f"{app.status} → {new_status}",
                        )
                    )
                update_application(changed)
                st.success("Saved.")
                st.rerun()

            if app.events:
                with st.expander("Events"):
                    for ev in reversed(app.events):
                        st.caption(f"{ev.timestamp.strftime('%Y-%m-%d %H:%M')}  **{ev.event}**  {ev.details}")


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
        )
        save_profile(updated)
        st.success("Profile saved to `config/profile.yaml`.")


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
        )
        save_settings(updated)
        st.success("Settings saved to `config/settings.yaml`.")


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
