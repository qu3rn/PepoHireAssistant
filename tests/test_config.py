"""Tests for config load/save helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from cv_sender.config import (
    LMStudioConfig,
    Profile,
    Settings,
    load_profile,
    load_settings,
    save_profile,
    save_settings,
)


# ---------------------------------------------------------------------------
# Profile round-trip
# ---------------------------------------------------------------------------


def test_save_and_load_profile(tmp_path: Path) -> None:
    path = tmp_path / "profile.yaml"
    profile = Profile(
        first_name="Jan",
        last_name="Kowalski",
        email="jan@example.com",
        phone="+48 000 000 000",
        city="Warszawa",
        expected_salary_b2b=25_000,
        expected_salary_uop=18_000,
    )
    save_profile(profile, path=str(path))
    loaded = load_profile(path=str(path))

    assert loaded.first_name == "Jan"
    assert loaded.last_name == "Kowalski"
    assert loaded.email == "jan@example.com"
    assert loaded.expected_salary_b2b == 25_000
    assert loaded.expected_salary_uop == 18_000


def test_load_profile_returns_defaults_when_missing(tmp_path: Path) -> None:
    missing = tmp_path / "no_profile.yaml"
    profile = load_profile(path=str(missing))
    assert isinstance(profile, Profile)
    assert profile.first_name == ""
    assert profile.email == ""


def test_save_profile_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "profile.yaml"
    profile = Profile(first_name="Test")
    save_profile(profile, path=str(path))
    assert path.exists()


def test_profile_full_name_property() -> None:
    profile = Profile(first_name="Jan", last_name="Kowalski")
    assert profile.full_name == "Jan Kowalski"


def test_profile_full_name_empty() -> None:
    profile = Profile()
    assert profile.full_name == ""


# ---------------------------------------------------------------------------
# Settings round-trip
# ---------------------------------------------------------------------------


def test_save_and_load_settings(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    settings = Settings(
        role="Frontend Developer",
        technologies=["React", "TypeScript"],
        min_salary_b2b=20_000,
        min_salary_uop=14_000,
        locations=["Warszawa", "Kraków"],
        auto_apply_min_score=75,
        require_manual_confirm=True,
        skip_without_salary=True,
    )
    save_settings(settings, path=str(path))
    loaded = load_settings(path=str(path))

    assert loaded.role == "Frontend Developer"
    assert loaded.technologies == ["React", "TypeScript"]
    assert loaded.min_salary_b2b == 20_000
    assert loaded.locations == ["Warszawa", "Kraków"]
    assert loaded.auto_apply_min_score == 75
    assert loaded.skip_without_salary is True


def test_load_settings_returns_defaults_when_missing(tmp_path: Path) -> None:
    missing = tmp_path / "no_settings.yaml"
    settings = load_settings(path=str(missing))
    assert isinstance(settings, Settings)
    assert settings.role == ""
    assert settings.require_manual_confirm is True


def test_save_settings_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "settings.yaml"
    settings = Settings(role="Backend Dev")
    save_settings(settings, path=str(path))
    assert path.exists()


def test_save_and_load_lm_studio_config(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    settings = Settings(
        lm_studio=LMStudioConfig(
            enabled=False,
            base_url="http://localhost:9999/v1",
            model="my-local-model",
        )
    )
    save_settings(settings, path=str(path))
    loaded = load_settings(path=str(path))

    assert loaded.lm_studio.enabled is False
    assert loaded.lm_studio.base_url == "http://localhost:9999/v1"
    assert loaded.lm_studio.model == "my-local-model"


def test_job_search_collector_mode_defaults_to_playwright() -> None:
    settings = Settings()
    assert settings.job_search.collector_mode == "playwright"
    assert settings.job_search.fallback_to_playwright is True
