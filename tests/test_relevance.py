from __future__ import annotations

from cv_sender.collectors.base import JobSearchCriteria
from cv_sender.relevance import EmergencyReactMode, match_offer_relevance


def _criteria() -> JobSearchCriteria:
    return JobSearchCriteria(
        keywords=["React Developer", "Frontend Developer", "Frontend Engineer"],
        technologies=["React", "TypeScript", "JavaScript", "Next.js"],
        locations=[],
        seniority=[],
        contract_types=[],
        min_salary_b2b=0,
        require_salary=False,
        max_offers_per_source=30,
        max_total_offers=100,
        exclude_keywords=[],
        request_delay_seconds=0,
    )


def _emergency_mode() -> EmergencyReactMode:
    return EmergencyReactMode(
        enabled=True,
        accept_needs_review=True,
        reject_obvious_non_it=True,
        min_relevance_score=50,
        needs_review_score=25,
    )


def test_relevance_senior_frontend_react_developer() -> None:
    r = match_offer_relevance(
        {
            "source": "justjoin",
            "url": "https://justjoin.it/job-offer/senior-frontend-react-developer",
            "title": "Senior Frontend React Developer",
        },
        _criteria(),
        emergency_mode=_emergency_mode(),
    )
    assert r.decision == "relevant"
    assert r.score >= 50


def test_relevance_programista_frontend_react() -> None:
    r = match_offer_relevance(
        {
            "source": "justjoin",
            "url": "https://justjoin.it/job-offer/programista-frontend-react",
            "title": "Programista Frontend React",
        },
        _criteria(),
        emergency_mode=_emergency_mode(),
    )
    assert r.decision == "relevant"


def test_relevance_reactjs_typescript() -> None:
    r = match_offer_relevance(
        {
            "source": "rocketjobs",
            "url": "https://rocketjobs.pl/oferta-pracy/react-js-developer-typescript",
            "title": "React.js Developer TypeScript",
        },
        _criteria(),
        emergency_mode=_emergency_mode(),
    )
    assert r.decision == "relevant"


def test_relevance_sprzedawca_kasjer_is_irrelevant_in_emergency_mode() -> None:
    r = match_offer_relevance(
        {
            "source": "rocketjobs",
            "url": "https://rocketjobs.pl/oferta-pracy/sprzedawca-kasjer-biedronka",
            "title": "Sprzedawca Kasjer",
        },
        _criteria(),
        emergency_mode=_emergency_mode(),
    )
    assert r.decision == "irrelevant"


def test_relevance_angular_developer() -> None:
    r = match_offer_relevance(
        {
            "source": "justjoin",
            "url": "https://justjoin.it/job-offer/angular-developer",
            "title": "Angular Developer",
        },
        _criteria(),
        emergency_mode=_emergency_mode(),
    )
    assert r.decision in {"needs_review", "irrelevant"}


def test_relevance_fullstack_dotnet_react() -> None:
    r = match_offer_relevance(
        {
            "source": "pracuj",
            "url": "https://www.pracuj.pl/praca/fullstack-developer-net-react,oferta,1004851255",
            "title": "Fullstack .NET + React",
        },
        _criteria(),
        emergency_mode=_emergency_mode(),
    )
    assert r.decision in {"needs_review", "relevant"}
    assert ".net" in " ".join(r.rejected_keywords) or "Mixed stack" in " ".join(r.warnings)


def test_relevance_frontend_vue_developer() -> None:
    r = match_offer_relevance(
        {
            "source": "nofluffjobs",
            "url": "https://nofluffjobs.com/job/frontend-vue-developer",
            "title": "Frontend Vue Developer",
        },
        _criteria(),
        emergency_mode=_emergency_mode(),
    )
    assert r.decision in {"needs_review", "irrelevant"}
