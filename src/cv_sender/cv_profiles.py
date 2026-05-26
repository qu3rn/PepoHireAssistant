"""CV profile loading, selection, and validation.

Responsibilities
----------------
- :class:`CVProfile`          — data model for one CV variant.
- :class:`CVSelectionResult`  — outcome of automatic CV selection for an offer.
- :func:`load_cv_profiles`    — load profiles from a :class:`~cv_sender.config.Profile`.
- :func:`select_cv_for_offer_object` — deterministic (+ optional LLM) CV selection.
- :func:`validate_cv_profiles` — return a list of warning strings for misconfigured CVs.

Backward compatibility
----------------------
If ``profile.cv_profiles`` is empty and ``profile.cv_path`` is set, a single
synthetic ``CVProfile`` with id ``"default"`` is generated transparently so
callers never need to handle both cases.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from cv_sender.models import Offer


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class CVProfile(BaseModel):
    """One CV variant with selection criteria."""

    id: str
    name: str = ""
    path: str = ""
    target_roles: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    seniority: list[str] = Field(default_factory=list)
    priority: int = 50
    active: bool = True


class CVSelectionResult(BaseModel):
    """Result of automatic CV selection for one offer."""

    selected_cv_id: str = ""
    selected_cv_name: str = ""
    selected_cv_path: str = ""
    score: int = 0
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Score weights
# ---------------------------------------------------------------------------

_ROLE_MATCH_SCORE = 40
_TECH_MATCH_PER_ITEM = 10
_TECH_MATCH_MAX = 40
_SENIORITY_MATCH_SCORE = 15
_PRIORITY_SCALE = 10        # priority / 10 added as bonus (0–100 → 0–10)
_PENALTY_MISSING_FILE = 100
_PENALTY_INACTIVE = 100


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------


def load_cv_profiles(profile: "Profile") -> list[CVProfile]:  # type: ignore[name-defined]
    """Return the list of :class:`CVProfile` objects from *profile*.

    When ``cv_profiles`` is empty, synthesises one profile from ``cv_path``
    (backward-compatible behaviour).
    """
    from cv_sender.config import Profile  # noqa: PLC0415 – avoid circular

    if not isinstance(profile, Profile):
        raise TypeError(f"Expected Profile, got {type(profile)}")

    if profile.cv_profiles:
        return [CVProfile.model_validate(p) for p in profile.cv_profiles]

    # Backward-compat: wrap the legacy cv_path
    if profile.cv_path:
        return [
            CVProfile(
                id="default",
                name="Default CV",
                path=profile.cv_path,
                active=True,
                priority=50,
            )
        ]

    return []


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _score_cv(cv: CVProfile, offer: Offer) -> tuple[int, list[str]]:
    """Compute a numeric score and a list of matching reasons for *cv* vs *offer*."""
    score = 0
    reasons: list[str] = []

    # Hard penalties
    if not cv.active:
        return -_PENALTY_INACTIVE, ["CV is inactive"]

    if cv.path and not Path(cv.path).exists():
        score -= _PENALTY_MISSING_FILE
        reasons.append(f"CV file not found: {cv.path}")

    # Role match
    offer_text = f"{offer.title} {offer.description}".lower()
    for role in cv.target_roles:
        if role.lower() in offer_text:
            score += _ROLE_MATCH_SCORE
            reasons.append(f"Role match: {role!r}")
            break  # count only once

    # Technology match
    tech_score = 0
    offer_techs = {t.lower() for t in offer.technologies}
    for tech in cv.technologies:
        if tech.lower() in offer_techs or tech.lower() in offer_text:
            tech_score += _TECH_MATCH_PER_ITEM
            reasons.append(f"Tech match: {tech!r}")
    score += min(tech_score, _TECH_MATCH_MAX)

    # Seniority match (infer from title)
    for level in cv.seniority:
        if level.lower() in offer.title.lower():
            score += _SENIORITY_MATCH_SCORE
            reasons.append(f"Seniority match: {level!r}")
            break

    # Priority bonus
    priority_bonus = cv.priority // _PRIORITY_SCALE
    if priority_bonus:
        score += priority_bonus
        reasons.append(f"Priority bonus: +{priority_bonus}")

    return score, reasons


def select_cv_for_offer_object(
    offer: Offer,
    profiles: list[CVProfile],
    default_cv_id: str = "",
) -> CVSelectionResult:
    """Select the best CV for *offer* from *profiles*.

    Tie-breaking order:
    1. Highest numeric score.
    2. Highest ``priority`` value.
    3. Profile matching ``default_cv_id``.
    4. First active profile in list order.

    Returns a :class:`CVSelectionResult` with ``selected_cv_id=""`` when
    *profiles* is empty.
    """
    if not profiles:
        return CVSelectionResult(
            warnings=["No CV profiles configured. Add cv_profiles to config/profile.yaml."]
        )

    active = [cv for cv in profiles if cv.active]
    if not active:
        return CVSelectionResult(
            warnings=["All CV profiles are inactive."]
        )

    scored: list[tuple[int, CVProfile, list[str]]] = []
    all_warnings: list[str] = []

    for cv in active:
        s, reasons = _score_cv(cv, offer)
        if cv.path and not Path(cv.path).exists():
            all_warnings.append(f"CV file missing: {cv.path}")
        scored.append((s, cv, reasons))

    # Sort: highest score first, then highest priority, then default first
    def _sort_key(item: tuple[int, CVProfile, list[str]]) -> tuple[int, int, int]:
        score, cv, _ = item
        is_default = 1 if cv.id == default_cv_id else 0
        return (score, cv.priority, is_default)

    scored.sort(key=_sort_key, reverse=True)

    best_score, best_cv, best_reasons = scored[0]

    result = CVSelectionResult(
        selected_cv_id=best_cv.id,
        selected_cv_name=best_cv.name or best_cv.id,
        selected_cv_path=best_cv.path,
        score=best_score,
        reasons=best_reasons,
        warnings=all_warnings,
    )

    if best_score < 0:
        result.warnings.append(
            f"Best available CV ({best_cv.name or best_cv.id}) has a negative score "
            "– check that the file exists and the profile is active."
        )

    return result


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_cv_profiles(profiles: list[CVProfile]) -> list[str]:
    """Return a list of warning strings describing problems in *profiles*.

    Returns an empty list when everything is fine.
    """
    warnings: list[str] = []
    seen_ids: set[str] = set()

    for cv in profiles:
        prefix = f"CV '{cv.id}'"

        if cv.id in seen_ids:
            warnings.append(f"{prefix}: duplicate id.")
        seen_ids.add(cv.id)

        if not cv.path:
            warnings.append(f"{prefix}: no path configured.")
        elif not Path(cv.path).exists():
            warnings.append(f"{prefix}: file not found at {cv.path!r}.")

        if not cv.active:
            warnings.append(f"{prefix}: inactive.")

        if not cv.name:
            warnings.append(f"{prefix}: no name set (id used as fallback).")

    return warnings
