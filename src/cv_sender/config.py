"""Configuration loading from YAML files."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


class Consents(BaseModel):
    """GDPR consent flags."""

    data_processing: bool = False
    future_recruitment: bool = False
    marketing: bool = False


class Profile(BaseModel):
    """Applicant profile used to fill forms."""

    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    cv_path: str = ""
    expected_salary_b2b: int | None = None
    expected_salary_uop: int | None = None
    availability: str = ""
    notice_period: str = ""
    english_level: str = ""
    preferred_work_mode: str = ""
    consents: Consents = Consents()

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class LMStudioConfig(BaseModel):
    """Settings for the local LM Studio server."""

    enabled: bool = True
    base_url: str = "http://localhost:1234/v1"
    api_key: str = "lm-studio"
    model: str = "local-model"


class Settings(BaseModel):
    """Application-wide search and scoring settings."""

    role: str = ""
    technologies: list[str] = []
    min_salary_b2b: int | None = None
    min_salary_uop: int | None = None
    locations: list[str] = []
    contract_types: list[str] = []
    auto_apply_min_score: int = 70
    require_manual_confirm: bool = True
    skip_without_salary: bool = False
    lm_studio: LMStudioConfig = LMStudioConfig()


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

_DEFAULT_PROFILE = Path("config/profile.yaml")
_DEFAULT_SETTINGS = Path("config/settings.yaml")


def load_profile(path: str | None = None) -> Profile:
    """Load profile from YAML file. Falls back to defaults if file is missing."""
    file_path = Path(path or os.getenv("PROFILE_PATH", str(_DEFAULT_PROFILE)))
    if not file_path.exists():
        return Profile()
    with file_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return Profile.model_validate(data)


def load_settings(path: str | None = None) -> Settings:
    """Load settings from YAML file. Falls back to defaults if file is missing."""
    file_path = Path(path or os.getenv("SETTINGS_PATH", str(_DEFAULT_SETTINGS)))
    if not file_path.exists():
        return Settings()
    with file_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    # Nested LMStudioConfig
    if "lm_studio" in data and isinstance(data["lm_studio"], dict):
        data["lm_studio"] = LMStudioConfig.model_validate(data["lm_studio"])
    return Settings.model_validate(data)


# ---------------------------------------------------------------------------
# Savers
# ---------------------------------------------------------------------------


def save_profile(profile: Profile, path: str | None = None) -> None:
    """Persist *profile* to a YAML file."""
    file_path = Path(path or os.getenv("PROFILE_PATH", str(_DEFAULT_PROFILE)))
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as fh:
        yaml.dump(profile.model_dump(), fh, allow_unicode=True, sort_keys=False)


def save_settings(settings: Settings, path: str | None = None) -> None:
    """Persist *settings* to a YAML file."""
    file_path = Path(path or os.getenv("SETTINGS_PATH", str(_DEFAULT_SETTINGS)))
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as fh:
        yaml.dump(settings.model_dump(), fh, allow_unicode=True, sort_keys=False)
