"""Deterministic and LLM-assisted offer scoring."""

from __future__ import annotations

from cv_sender.config import Settings
from cv_sender.models import Decision, Offer

# ---------------------------------------------------------------------------
# Deterministic scoring
# ---------------------------------------------------------------------------

_SCORE_ROLE_MATCH = 30
_SCORE_REACT = 20
_SCORE_TYPESCRIPT = 20
_SCORE_NEXTJS = 10
_SCORE_SALARY_OK = 20
_SCORE_LOCATION_OK = 10
_PENALTY_NO_SALARY = -30
_PENALTY_SENIORITY_MISMATCH = -40

_SENIOR_KEYWORDS = {"senior", "lead", "principal", "staff", "architect"}
_JUNIOR_KEYWORDS = {"junior", "intern", "trainee", "stażysta"}


def _tech_haystack(offer: Offer) -> str:
    """Return a lower-case string combining all technology signals from *offer*."""
    parts = list(offer.technologies) + [offer.title, offer.description]
    return " ".join(parts).lower()


def score_offer_deterministic(
    offer: Offer, settings: Settings
) -> tuple[int, list[str], list[str]]:
    """Compute a deterministic score for *offer* given *settings*.

    Returns a 3-tuple of ``(score, reasons, risks)``.
    """
    score = 0
    reasons: list[str] = []
    risks: list[str] = []

    haystack = _tech_haystack(offer)
    title_lower = offer.title.lower()
    role_lower = settings.role.lower()

    # +30 if required role matches title
    if settings.role and settings.role.lower() in title_lower:
        score += _SCORE_ROLE_MATCH
        reasons.append(f"Role '{settings.role}' found in title")

    # +20 if React is present
    if "react" in haystack:
        score += _SCORE_REACT
        reasons.append("React present")

    # +20 if TypeScript is present
    if "typescript" in haystack:
        score += _SCORE_TYPESCRIPT
        reasons.append("TypeScript present")

    # +10 if Next.js is present
    if "next.js" in haystack or "nextjs" in haystack:
        score += _SCORE_NEXTJS
        reasons.append("Next.js present")

    # +20 if salary_min meets minimum expectation
    if offer.salary_min is not None:
        salary_ok = False
        contract_lower = offer.contract.lower()
        if "b2b" in contract_lower and settings.min_salary_b2b:
            if offer.salary_min >= settings.min_salary_b2b:
                salary_ok = True
                reasons.append("Salary meets B2B minimum")
        elif settings.min_salary_uop:
            if offer.salary_min >= settings.min_salary_uop:
                salary_ok = True
                reasons.append("Salary meets UoP minimum")
        if salary_ok:
            score += _SCORE_SALARY_OK

    # +10 if remote or preferred location matches
    location_lower = offer.location.lower()
    if "remote" in location_lower or "zdalnie" in location_lower:
        score += _SCORE_LOCATION_OK
        reasons.append("Remote work available")
    elif settings.locations and any(
        loc.lower() in location_lower for loc in settings.locations
    ):
        score += _SCORE_LOCATION_OK
        reasons.append("Location matches preference")

    # -30 if salary is missing and skip_without_salary is enabled
    if offer.salary_min is None and settings.skip_without_salary:
        score += _PENALTY_NO_SALARY
        reasons.append("No salary information (penalty applied)")

    # -40 if seniority clearly does not match
    # Only penalise when both the offer and the settings role carry explicit,
    # conflicting seniority signals (e.g. junior role vs. senior offer).
    role_is_junior = bool(_JUNIOR_KEYWORDS & set(role_lower.split()))
    role_is_senior = bool(_SENIOR_KEYWORDS & set(role_lower.split()))

    if _SENIOR_KEYWORDS & set(title_lower.split()) and role_is_junior:
        score += _PENALTY_SENIORITY_MISMATCH
        risks.append("Seniority mismatch: offer targets senior-level candidates")
    elif _JUNIOR_KEYWORDS & set(title_lower.split()) and role_is_senior:
        score += _PENALTY_SENIORITY_MISMATCH
        risks.append("Seniority mismatch: offer targets junior-level candidates")

    return score, reasons, risks


# ---------------------------------------------------------------------------
# Decision helper
# ---------------------------------------------------------------------------


def decide(score: int, settings: Settings) -> Decision:
    """Map a numeric *score* to a :class:`Decision`."""
    if score >= settings.auto_apply_min_score:
        return Decision.APPLY
    if score >= settings.auto_apply_min_score // 2:
        return Decision.MAYBE
    return Decision.SKIP


# ---------------------------------------------------------------------------
# Full scoring pipeline
# ---------------------------------------------------------------------------


def score_offer(offer: Offer, settings: Settings, llm_result: dict | None = None) -> Offer:
    """Score *offer* and return an updated copy.

    If *llm_result* is provided it is merged with the deterministic result:
    the LLM score overrides the deterministic one, but deterministic reasons
    are always kept.
    """
    det_score, det_reasons, det_risks = score_offer_deterministic(offer, settings)

    final_score = det_score
    final_reasons = list(det_reasons)
    final_risks = list(det_risks)
    final_decision: Decision | None = None

    if llm_result:
        try:
            final_score = int(llm_result.get("score", det_score))
            llm_reasons = llm_result.get("reasons", [])
            llm_risks = llm_result.get("risks", [])
            if isinstance(llm_reasons, list):
                final_reasons.extend(llm_reasons)
            if isinstance(llm_risks, list):
                final_risks.extend(llm_risks)
            raw_decision = llm_result.get("decision", "")
            try:
                final_decision = Decision(raw_decision)
            except ValueError:
                pass  # Unknown decision value – fall back to deterministic
        except (ValueError, TypeError):
            pass  # Fall back to deterministic values

    if final_decision is None:
        final_decision = decide(final_score, settings)

    return offer.model_copy(
        update={
            "score": final_score,
            "decision": final_decision,
            "decision_reasons": final_reasons,
            "risks": final_risks,
        }
    )
