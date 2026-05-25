"""Tests for deterministic offer scoring."""

from __future__ import annotations

import pytest

from cv_sender.config import LMStudioConfig, Settings
from cv_sender.models import Decision, Offer
from cv_sender.scorer import decide, score_offer, score_offer_deterministic

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_offer(**kwargs) -> Offer:
    defaults = dict(
        url="https://example.com/job/1",
        title="Frontend Developer",
        company="ACME",
    )
    defaults.update(kwargs)
    return Offer(**defaults)


def _make_settings(**kwargs) -> Settings:
    defaults = dict(
        role="Frontend Developer",
        technologies=["React", "TypeScript", "Next.js"],
        min_salary_b2b=18_000,
        min_salary_uop=12_000,
        locations=["Warszawa"],
        auto_apply_min_score=70,
        skip_without_salary=False,
        lm_studio=LMStudioConfig(enabled=False),
    )
    defaults.update(kwargs)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# Role matching
# ---------------------------------------------------------------------------


def test_role_match_adds_30_points() -> None:
    offer = _make_offer(title="Senior Frontend Developer")
    settings = _make_settings(role="Frontend Developer")
    score, reasons, _ = score_offer_deterministic(offer, settings)
    assert score >= 30
    assert any("Role" in r for r in reasons)


def test_role_no_match_adds_zero() -> None:
    offer = _make_offer(title="Backend Engineer")
    settings = _make_settings(role="Frontend Developer")
    score, reasons, _ = score_offer_deterministic(offer, settings)
    assert not any("Role" in r for r in reasons)


# ---------------------------------------------------------------------------
# Technology scoring
# ---------------------------------------------------------------------------


def test_react_adds_20_points() -> None:
    offer = _make_offer(technologies=["React", "Node.js"])
    settings = _make_settings(role="")
    score, reasons, _ = score_offer_deterministic(offer, settings)
    assert score >= 20
    assert any("React" in r for r in reasons)


def test_typescript_adds_20_points() -> None:
    offer = _make_offer(technologies=["TypeScript"])
    settings = _make_settings(role="")
    score, reasons, _ = score_offer_deterministic(offer, settings)
    assert score >= 20
    assert any("TypeScript" in r for r in reasons)


def test_nextjs_adds_10_points() -> None:
    offer = _make_offer(technologies=["Next.js"])
    settings = _make_settings(role="")
    score, reasons, _ = score_offer_deterministic(offer, settings)
    assert score >= 10
    assert any("Next.js" in r for r in reasons)


def test_full_tech_stack_scores_50() -> None:
    offer = _make_offer(technologies=["React", "TypeScript", "Next.js"])
    settings = _make_settings(role="")
    score, _, _ = score_offer_deterministic(offer, settings)
    assert score == 50  # 20 + 20 + 10


# ---------------------------------------------------------------------------
# Salary scoring
# ---------------------------------------------------------------------------


def test_salary_b2b_meets_min_adds_20() -> None:
    offer = _make_offer(salary_min=20_000, contract="B2B")
    settings = _make_settings(min_salary_b2b=18_000, role="")
    score, reasons, _ = score_offer_deterministic(offer, settings)
    assert score >= 20
    assert any("B2B" in r for r in reasons)


def test_salary_b2b_below_min_no_bonus() -> None:
    offer = _make_offer(salary_min=10_000, contract="B2B")
    settings = _make_settings(min_salary_b2b=18_000, role="")
    score, reasons, _ = score_offer_deterministic(offer, settings)
    assert not any("Salary meets" in r for r in reasons)


def test_no_salary_penalty_when_skip_enabled() -> None:
    offer = _make_offer(salary_min=None)
    settings = _make_settings(skip_without_salary=True, role="")
    score, reasons, _ = score_offer_deterministic(offer, settings)
    assert score < 0
    assert any("penalty" in r.lower() for r in reasons)


def test_no_salary_no_penalty_when_skip_disabled() -> None:
    offer = _make_offer(salary_min=None)
    settings = _make_settings(skip_without_salary=False, role="")
    score, _, _ = score_offer_deterministic(offer, settings)
    assert score >= 0


# ---------------------------------------------------------------------------
# Location scoring
# ---------------------------------------------------------------------------


def test_remote_adds_10_points() -> None:
    offer = _make_offer(location="remote")
    settings = _make_settings(role="")
    score, reasons, _ = score_offer_deterministic(offer, settings)
    assert score >= 10
    assert any("Remote" in r for r in reasons)


def test_preferred_location_adds_10_points() -> None:
    offer = _make_offer(location="Warszawa")
    settings = _make_settings(locations=["Warszawa"], role="")
    score, reasons, _ = score_offer_deterministic(offer, settings)
    assert score >= 10
    assert any("Location" in r for r in reasons)


# ---------------------------------------------------------------------------
# Seniority mismatch
# ---------------------------------------------------------------------------


def test_senior_mismatch_penalty() -> None:
    # Explicit conflict: settings role says "Junior", offer is "Senior"
    offer = _make_offer(title="Senior Software Engineer")
    settings = _make_settings(role="Junior Developer")
    score, _, risks = score_offer_deterministic(offer, settings)
    assert any("seniority" in r.lower() for r in risks)


# ---------------------------------------------------------------------------
# decide()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score,expected",
    [
        (80, Decision.APPLY),
        (70, Decision.APPLY),
        (50, Decision.MAYBE),
        (10, Decision.SKIP),
        (0, Decision.SKIP),
    ],
)
def test_decide(score: int, expected: Decision) -> None:
    settings = _make_settings(auto_apply_min_score=70)
    assert decide(score, settings) == expected


# ---------------------------------------------------------------------------
# score_offer() – full pipeline
# ---------------------------------------------------------------------------


def test_score_offer_returns_updated_offer() -> None:
    offer = _make_offer(technologies=["React", "TypeScript"], salary_min=20_000, contract="B2B")
    settings = _make_settings()
    result = score_offer(offer, settings)
    assert result.score is not None
    assert result.decision is not None
    assert len(result.decision_reasons) > 0


def test_score_offer_with_llm_result_overrides_score() -> None:
    offer = _make_offer()
    settings = _make_settings()
    llm_result = {
        "score": 95,
        "decision": "apply",
        "reasons": ["LLM says great fit"],
        "risks": [],
    }
    result = score_offer(offer, settings, llm_result)
    assert result.score == 95
    assert result.decision == Decision.APPLY
    assert "LLM says great fit" in result.decision_reasons


def test_score_offer_handles_invalid_llm_result_gracefully() -> None:
    offer = _make_offer(technologies=["React"])
    settings = _make_settings()
    bad_llm = {"invalid": "data"}
    # Should not raise; falls back to deterministic values
    result = score_offer(offer, settings, bad_llm)
    assert result.score is not None
