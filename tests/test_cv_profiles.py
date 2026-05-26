"""Tests for cv_profiles module – CVProfile, scoring, selection, validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cv_sender.config import Profile
from cv_sender.cv_profiles import (
    CVProfile,
    CVSelectionResult,
    _score_cv,
    load_cv_profiles,
    select_cv_for_offer_object,
    validate_cv_profiles,
)
from cv_sender.models import Offer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_offer(**kwargs) -> Offer:
    defaults = dict(
        id="offer-1",
        url="https://example.com/job/1",
        title="Senior React Developer",
        source="manual",
        company="ACME",
        description="We need a React expert with TypeScript experience",
        technologies=["React", "TypeScript", "CSS"],
    )
    defaults.update(kwargs)
    return Offer(**defaults)


def _make_cv(**kwargs) -> CVProfile:
    defaults = dict(
        id="cv-1",
        name="React CV",
        path="data/cv.pdf",  # doesn't exist by default
        target_roles=["React Developer", "Frontend Developer"],
        technologies=["React", "TypeScript"],
        seniority=["Senior", "Mid"],
        priority=50,
        active=True,
    )
    defaults.update(kwargs)
    return CVProfile(**defaults)


def _make_profile(**kwargs) -> Profile:
    defaults = dict(
        first_name="Jan",
        last_name="Kowalski",
        email="jan@example.com",
    )
    defaults.update(kwargs)
    return Profile(**defaults)


# ---------------------------------------------------------------------------
# CVProfile model
# ---------------------------------------------------------------------------


class TestCVProfile:
    def test_defaults(self):
        cv = CVProfile(id="x")
        assert cv.active is True
        assert cv.priority == 50
        assert cv.target_roles == []
        assert cv.technologies == []
        assert cv.seniority == []

    def test_custom_fields(self):
        cv = _make_cv(id="my-cv", priority=80)
        assert cv.id == "my-cv"
        assert cv.priority == 80


# ---------------------------------------------------------------------------
# load_cv_profiles
# ---------------------------------------------------------------------------


class TestLoadCvProfiles:
    def test_empty_profile_no_cv_path(self):
        profile = _make_profile()
        result = load_cv_profiles(profile)
        assert result == []

    def test_legacy_cv_path_synthesises_default(self):
        profile = _make_profile(cv_path="data/cv.pdf")
        result = load_cv_profiles(profile)
        assert len(result) == 1
        assert result[0].id == "default"
        assert result[0].path == "data/cv.pdf"
        assert result[0].active is True

    def test_cv_profiles_list_overrides_cv_path(self):
        profile = _make_profile(
            cv_path="data/cv.pdf",
            cv_profiles=[
                {"id": "frontend", "name": "Frontend CV", "path": "data/frontend.pdf"},
            ],
        )
        result = load_cv_profiles(profile)
        assert len(result) == 1
        assert result[0].id == "frontend"
        assert result[0].path == "data/frontend.pdf"

    def test_multiple_cv_profiles(self):
        profile = _make_profile(
            cv_profiles=[
                {"id": "cv1", "name": "CV 1", "path": "data/cv1.pdf"},
                {"id": "cv2", "name": "CV 2", "path": "data/cv2.pdf"},
            ]
        )
        result = load_cv_profiles(profile)
        assert len(result) == 2
        assert result[0].id == "cv1"
        assert result[1].id == "cv2"

    def test_requires_profile_instance(self):
        with pytest.raises(TypeError):
            load_cv_profiles({"cv_path": "x"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _score_cv
# ---------------------------------------------------------------------------


class TestScoreCv:
    def test_inactive_penalty(self):
        cv = _make_cv(active=False)
        offer = _make_offer()
        score, reasons = _score_cv(cv, offer)
        assert score == -100
        assert any("inactive" in r.lower() for r in reasons)

    def test_missing_file_penalty(self, tmp_path):
        cv = _make_cv(path=str(tmp_path / "nonexistent.pdf"))
        offer = _make_offer()
        score, reasons = _score_cv(cv, offer)
        assert score < 0
        assert any("not found" in r.lower() for r in reasons)

    def test_role_match_adds_40(self, tmp_path):
        cv_file = tmp_path / "cv.pdf"
        cv_file.write_bytes(b"%PDF")
        cv = _make_cv(
            path=str(cv_file),
            target_roles=["React Developer"],
            technologies=[],
            seniority=[],
            priority=0,
        )
        offer = _make_offer(title="React Developer", description="", technologies=[])
        score, reasons = _score_cv(cv, offer)
        assert score == 40
        assert any("Role match" in r for r in reasons)

    def test_role_only_counted_once(self, tmp_path):
        cv_file = tmp_path / "cv.pdf"
        cv_file.write_bytes(b"%PDF")
        cv = _make_cv(
            path=str(cv_file),
            target_roles=["React Developer", "React Developer"],  # duplicate
            technologies=[],
            seniority=[],
            priority=0,
        )
        offer = _make_offer(title="React Developer", description="", technologies=[])
        score, reasons = _score_cv(cv, offer)
        assert score == 40  # only one match counted

    def test_tech_match_adds_10_each(self, tmp_path):
        cv_file = tmp_path / "cv.pdf"
        cv_file.write_bytes(b"%PDF")
        cv = _make_cv(
            path=str(cv_file),
            target_roles=[],
            technologies=["React", "TypeScript"],
            seniority=[],
            priority=0,
        )
        offer = _make_offer(technologies=["React", "TypeScript"])
        score, reasons = _score_cv(cv, offer)
        assert score == 20
        assert sum(1 for r in reasons if "Tech match" in r) == 2

    def test_tech_match_capped_at_40(self, tmp_path):
        cv_file = tmp_path / "cv.pdf"
        cv_file.write_bytes(b"%PDF")
        cv = _make_cv(
            path=str(cv_file),
            target_roles=[],
            technologies=["React", "TypeScript", "Node.js", "CSS", "HTML", "Jest"],
            seniority=[],
            priority=0,
        )
        offer = _make_offer(technologies=["React", "TypeScript", "Node.js", "CSS", "HTML", "Jest"])
        score, reasons = _score_cv(cv, offer)
        assert score == 40  # capped

    def test_seniority_match_adds_15(self, tmp_path):
        cv_file = tmp_path / "cv.pdf"
        cv_file.write_bytes(b"%PDF")
        cv = _make_cv(
            path=str(cv_file),
            target_roles=[],
            technologies=[],
            seniority=["Senior"],
            priority=0,
        )
        offer = _make_offer(title="Senior Frontend Developer")
        score, reasons = _score_cv(cv, offer)
        assert score == 15
        assert any("Seniority match" in r for r in reasons)

    def test_priority_bonus(self, tmp_path):
        cv_file = tmp_path / "cv.pdf"
        cv_file.write_bytes(b"%PDF")
        cv = _make_cv(
            path=str(cv_file),
            target_roles=[],
            technologies=[],
            seniority=[],
            priority=100,  # bonus = 100 // 10 = 10
        )
        offer = _make_offer()
        score, reasons = _score_cv(cv, offer)
        assert score == 10
        assert any("Priority bonus" in r for r in reasons)

    def test_combined_all_matches(self, tmp_path):
        cv_file = tmp_path / "cv.pdf"
        cv_file.write_bytes(b"%PDF")
        cv = _make_cv(
            path=str(cv_file),
            target_roles=["React Developer"],
            technologies=["React", "TypeScript"],
            seniority=["Senior"],
            priority=50,  # bonus = 5
        )
        offer = _make_offer(
            title="Senior React Developer",
            technologies=["React", "TypeScript"],
        )
        score, _ = _score_cv(cv, offer)
        # 40 (role) + 20 (tech 2×10) + 15 (seniority) + 5 (priority) = 80
        assert score == 80


# ---------------------------------------------------------------------------
# select_cv_for_offer_object
# ---------------------------------------------------------------------------


class TestSelectCvForOffer:
    def test_empty_profiles_returns_warning(self):
        offer = _make_offer()
        result = select_cv_for_offer_object(offer, [])
        assert result.selected_cv_id == ""
        assert result.warnings

    def test_all_inactive_returns_warning(self):
        cv = _make_cv(active=False)
        offer = _make_offer()
        result = select_cv_for_offer_object(offer, [cv])
        assert result.warnings
        assert "inactive" in result.warnings[0].lower()

    def test_selects_best_match(self, tmp_path):
        cv_file1 = tmp_path / "cv1.pdf"
        cv_file1.write_bytes(b"%PDF")
        cv_file2 = tmp_path / "cv2.pdf"
        cv_file2.write_bytes(b"%PDF")

        cv_react = _make_cv(
            id="react",
            path=str(cv_file1),
            target_roles=["React Developer"],
            technologies=["React"],
            seniority=[],
            priority=50,
        )
        cv_backend = _make_cv(
            id="backend",
            path=str(cv_file2),
            target_roles=["Python Developer"],
            technologies=["Python"],
            seniority=[],
            priority=50,
        )
        offer = _make_offer(title="React Developer", technologies=["React"])
        result = select_cv_for_offer_object(offer, [cv_react, cv_backend])
        assert result.selected_cv_id == "react"

    def test_missing_file_warns_but_selects(self, tmp_path):
        cv_ok = _make_cv(
            id="ok", path=str(tmp_path / "ok.pdf"), target_roles=[], technologies=[], seniority=[], priority=50
        )
        (tmp_path / "ok.pdf").write_bytes(b"%PDF")
        cv_missing = _make_cv(
            id="missing", path=str(tmp_path / "nonexistent.pdf"), target_roles=["React Developer"],
            technologies=[], seniority=[], priority=50
        )
        offer = _make_offer(title="React Developer", technologies=[])
        result = select_cv_for_offer_object(offer, [cv_ok, cv_missing])
        # cv_ok is selected because missing has negative score
        assert result.selected_cv_id == "ok"

    def test_tiebreak_by_priority(self, tmp_path):
        cv1_file = tmp_path / "cv1.pdf"
        cv1_file.write_bytes(b"%PDF")
        cv2_file = tmp_path / "cv2.pdf"
        cv2_file.write_bytes(b"%PDF")

        cv_low = _make_cv(id="low", path=str(cv1_file), target_roles=[], technologies=[], seniority=[], priority=30)
        cv_high = _make_cv(id="high", path=str(cv2_file), target_roles=[], technologies=[], seniority=[], priority=80)
        offer = _make_offer(title="Generic Job", technologies=[])
        result = select_cv_for_offer_object(offer, [cv_low, cv_high])
        assert result.selected_cv_id == "high"

    def test_tiebreak_by_default_cv_id(self, tmp_path):
        cv1_file = tmp_path / "cv1.pdf"
        cv1_file.write_bytes(b"%PDF")
        cv2_file = tmp_path / "cv2.pdf"
        cv2_file.write_bytes(b"%PDF")

        # Both identical priority — default_cv_id should win
        cv_a = _make_cv(id="a", path=str(cv1_file), target_roles=[], technologies=[], seniority=[], priority=50)
        cv_b = _make_cv(id="b", path=str(cv2_file), target_roles=[], technologies=[], seniority=[], priority=50)
        offer = _make_offer(title="Generic Job", technologies=[])
        result = select_cv_for_offer_object(offer, [cv_a, cv_b], default_cv_id="b")
        assert result.selected_cv_id == "b"

    def test_result_fields_populated(self, tmp_path):
        cv_file = tmp_path / "cv.pdf"
        cv_file.write_bytes(b"%PDF")
        cv = _make_cv(id="cv1", name="My CV", path=str(cv_file), priority=50, target_roles=[], technologies=[], seniority=[])
        offer = _make_offer()
        result = select_cv_for_offer_object(offer, [cv])
        assert result.selected_cv_id == "cv1"
        assert result.selected_cv_name == "My CV"
        assert result.selected_cv_path == str(cv_file)

    def test_negative_score_warning(self, tmp_path):
        cv = _make_cv(id="cv1", path=str(tmp_path / "nonexistent.pdf"), target_roles=[], technologies=[], seniority=[], priority=0)
        offer = _make_offer()
        result = select_cv_for_offer_object(offer, [cv])
        assert any("negative score" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# validate_cv_profiles
# ---------------------------------------------------------------------------


class TestValidateCvProfiles:
    def test_empty_list(self):
        assert validate_cv_profiles([]) == []

    def test_no_path(self):
        cv = CVProfile(id="x", name="No path CV", path="")
        warnings = validate_cv_profiles([cv])
        assert any("no path" in w.lower() for w in warnings)

    def test_missing_file(self, tmp_path):
        cv = CVProfile(id="x", name="CV", path=str(tmp_path / "missing.pdf"))
        warnings = validate_cv_profiles([cv])
        assert any("not found" in w.lower() for w in warnings)

    def test_inactive_cv(self, tmp_path):
        cv_file = tmp_path / "cv.pdf"
        cv_file.write_bytes(b"%PDF")
        cv = CVProfile(id="x", name="CV", path=str(cv_file), active=False)
        warnings = validate_cv_profiles([cv])
        assert any("inactive" in w.lower() for w in warnings)

    def test_no_name(self, tmp_path):
        cv_file = tmp_path / "cv.pdf"
        cv_file.write_bytes(b"%PDF")
        cv = CVProfile(id="x", name="", path=str(cv_file))
        warnings = validate_cv_profiles([cv])
        assert any("no name" in w.lower() for w in warnings)

    def test_duplicate_id(self, tmp_path):
        cv_file = tmp_path / "cv.pdf"
        cv_file.write_bytes(b"%PDF")
        cv1 = CVProfile(id="dup", name="CV 1", path=str(cv_file))
        cv2 = CVProfile(id="dup", name="CV 2", path=str(cv_file))
        warnings = validate_cv_profiles([cv1, cv2])
        assert any("duplicate" in w.lower() for w in warnings)

    def test_valid_cv_no_warnings(self, tmp_path):
        cv_file = tmp_path / "cv.pdf"
        cv_file.write_bytes(b"%PDF")
        cv = CVProfile(id="x", name="My CV", path=str(cv_file), active=True)
        assert validate_cv_profiles([cv]) == []


# ---------------------------------------------------------------------------
# Application record tracks selected CV
# ---------------------------------------------------------------------------


class TestApplicationCvTracking:
    def test_application_has_cv_fields(self):
        from cv_sender.models import Application  # noqa: PLC0415

        app = Application(
            offer_id="o1",
            selected_cv_id="react",
            selected_cv_name="React CV",
            selected_cv_path="data/cv/react.pdf",
        )
        assert app.selected_cv_id == "react"
        assert app.selected_cv_name == "React CV"
        assert app.selected_cv_path == "data/cv/react.pdf"

    def test_application_defaults_empty_strings(self):
        from cv_sender.models import Application  # noqa: PLC0415

        app = Application(offer_id="o1")
        assert app.selected_cv_id == ""
        assert app.selected_cv_name == ""
        assert app.selected_cv_path == ""


# ---------------------------------------------------------------------------
# cv_path_override passed through to filler
# ---------------------------------------------------------------------------


class TestCvPathOverride:
    def test_filler_uses_cv_path_override(self):
        from cv_sender.config import Settings  # noqa: PLC0415
        from cv_sender.portals.generic import GenericFiller  # noqa: PLC0415

        profile = _make_profile(cv_path="data/old.pdf")
        settings = Settings()
        filler = GenericFiller(profile=profile, settings=settings, cv_path_override="data/new.pdf")
        assert filler.cv_path_override == "data/new.pdf"

    def test_filler_falls_back_to_profile_cv_path(self):
        from cv_sender.config import Settings  # noqa: PLC0415
        from cv_sender.portals.generic import GenericFiller  # noqa: PLC0415

        profile = _make_profile(cv_path="data/old.pdf")
        settings = Settings()
        filler = GenericFiller(profile=profile, settings=settings)
        assert filler.cv_path_override == ""
        assert filler.profile.cv_path == "data/old.pdf"

    def test_choose_filler_passes_cv_path_override(self):
        from cv_sender.config import Settings  # noqa: PLC0415
        from cv_sender.form_filler import _choose_filler  # noqa: PLC0415

        profile = _make_profile(cv_path="data/old.pdf")
        settings = Settings()
        filler = _choose_filler("https://rocketjobs.pl/job/1", profile, settings, cv_path_override="data/new.pdf")
        assert filler.cv_path_override == "data/new.pdf"


# ---------------------------------------------------------------------------
# Service layer – list_cv_profiles / validate_cv_profiles
# ---------------------------------------------------------------------------


class TestCvProfileServices:
    def test_list_cv_profiles_empty(self, tmp_path, monkeypatch):
        import cv_sender.services as svc  # noqa: PLC0415

        profile = _make_profile()
        monkeypatch.setattr(svc, "load_profile", lambda: profile)
        result = svc.list_cv_profiles()
        assert result == []

    def test_list_cv_profiles_from_legacy_cv_path(self, tmp_path, monkeypatch):
        import cv_sender.services as svc  # noqa: PLC0415

        profile = _make_profile(cv_path="data/cv.pdf")
        monkeypatch.setattr(svc, "load_profile", lambda: profile)
        result = svc.list_cv_profiles()
        assert len(result) == 1
        assert result[0].id == "default"

    def test_validate_cv_profiles_service(self, monkeypatch):
        import cv_sender.services as svc  # noqa: PLC0415

        profile = _make_profile(
            cv_profiles=[{"id": "cv1", "name": "", "path": "missing.pdf", "active": True}]
        )
        monkeypatch.setattr(svc, "load_profile", lambda: profile)
        warnings = svc.validate_cv_profiles()
        assert isinstance(warnings, list)
        # Should warn about missing file and missing name
        combined = " ".join(warnings).lower()
        assert "not found" in combined or "no name" in combined

    def test_select_cv_for_offer_missing_offer(self, monkeypatch):
        import cv_sender.services as svc  # noqa: PLC0415

        monkeypatch.setattr(svc, "get_offer_by_id", lambda _: None)
        result = svc.select_cv_for_offer("nonexistent")
        assert result.selected_cv_id == ""
        assert result.warnings
