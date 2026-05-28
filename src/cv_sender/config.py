"""Configuration loading from YAML files."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

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
    cv_path: str = ""  # Legacy single-CV path; used when cv_profiles is empty
    expected_salary_b2b: int | None = None
    expected_salary_uop: int | None = None
    availability: str = ""
    notice_period: str = ""
    english_level: str = ""
    preferred_work_mode: str = ""
    consents: Consents = Consents()
    # Multi-CV support
    default_cv_id: str = ""
    cv_profiles: list[dict] = []  # raw dicts; parsed by cv_profiles.load_cv_profiles()

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


class FormFillingConfig(BaseModel):
    """Settings for Playwright browser-based form filling."""

    debug: bool = False
    slow_mo_ms: int = 0
    headless: bool = False
    screenshot_on_failure: bool = True
    save_form_snapshot: bool = True
    save_step_log: bool = True


class PlaywrightModalConfig(BaseModel):
    """Safety-limited cookie/modal handling settings for Playwright pages."""

    enabled: bool = True
    cookie_mode: str = "reject_optional"  # accept_all | reject_optional | close_only | disabled
    close_newsletters: bool = True
    close_generic_overlays: bool = True
    max_attempts: int = 3
    timeout_ms: int = 3000


class PlaywrightConfig(BaseModel):
    """Shared Playwright behavior used by collectors/fillers/debug tools."""

    modals: PlaywrightModalConfig = PlaywrightModalConfig()


class AnswerProfileConfig(BaseModel):
    """Candidate facts used when generating application answers."""

    short_bio: str = ""
    years_experience: str = ""
    strongest_skills: list[str] = []
    industries: list[str] = []
    work_style: str = ""
    motivation_general: str = ""
    salary_b2b: str = ""
    salary_uop: str = ""
    english_level: str = ""


class AnswerTemplatesConfig(BaseModel):
    """Reusable fill-in-the-blank templates for common application questions."""

    why_company: str = (
        "I am interested in this role because it matches my experience with "
        "{technologies} and gives me a chance to work on {role} challenges."
    )
    react_experience: str = (
        "I have practical experience building frontend applications with "
        "React, TypeScript and modern UI tooling."
    )
    availability: str = "{availability}"
    salary: str = "{salary_expectation}"


class AnswerGenerationConfig(BaseModel):
    """Settings controlling automatic application answer generation."""

    enabled: bool = True
    use_llm: bool = True
    auto_fill_generated_answers: bool = True
    require_review_for_low_confidence: bool = True
    min_confidence_to_autofill: float = 0.65
    max_answer_chars: int = 600


class FollowUpConfig(BaseModel):
    """Settings for the follow-up reminder system."""

    enabled: bool = True
    default_follow_up_after_days: int = 5
    mark_no_response_after_days: int = 14
    show_due_within_days: int = 3
    allow_weekend_due_dates: bool = False


class GmailConfig(BaseModel):
    """Settings for the Gmail read-only integration."""

    enabled: bool = False
    credentials_path: str = "config/google_credentials.json"
    token_path: str = "config/google_token.json"
    readonly: bool = True
    scan_days_back: int = 30
    max_results: int = 100
    store_email_body: bool = False
    store_snippet: bool = True
    auto_update_status: bool = False


class CalendarConfig(BaseModel):
    """Settings for the Google Calendar integration."""

    enabled: bool = False
    credentials_path: str = "config/google_credentials.json"
    token_path: str = "config/google_calendar_token.json"
    calendar_id: str = "primary"
    timezone: str = "Europe/Warsaw"
    default_interview_duration_minutes: int = 60
    create_calendar_events: bool = False
    add_reminders: bool = True
    reminder_minutes_before: list[int] = Field(default_factory=lambda: [1440, 60])


class JobSearchSourceConfig(BaseModel):
    enabled: bool = True


class LanguageMatchingConfig(BaseModel):
    enabled: bool = True
    languages: list[str] = Field(default_factory=lambda: ["pl", "en"])


class EmergencyReactModeConfig(BaseModel):
    enabled: bool = False
    accept_needs_review: bool = True
    reject_obvious_non_it: bool = True
    min_relevance_score: int = 50
    needs_review_score: int = 25


class JobSearchConfig(BaseModel):
    """Settings for the automated job-offer collection feature."""

    enabled: bool = False
    collector_mode: str = "playwright"
    fallback_to_playwright: bool = True
    keywords: list[str] = Field(default_factory=lambda: ["React Developer", "Frontend Developer"])
    technologies: list[str] = Field(default_factory=lambda: ["React", "TypeScript", "Next.js"])
    locations: list[str] = Field(default_factory=lambda: ["Remote", "Poland"])
    seniority: list[str] = Field(default_factory=lambda: ["Mid", "Senior"])
    contract_types: list[str] = Field(default_factory=lambda: ["B2B", "UoP"])
    min_salary_b2b: int = 0
    require_salary: bool = False
    max_offers_per_source: int = 30
    max_total_offers: int = 100
    exclude_keywords: list[str] = Field(default_factory=lambda: ["Angular", "PHP", "WordPress"])
    request_delay_seconds: float = 1.5
    language_matching: LanguageMatchingConfig = LanguageMatchingConfig()
    emergency_react_mode: EmergencyReactModeConfig = EmergencyReactModeConfig()
    sources: dict[str, JobSearchSourceConfig] = Field(
        default_factory=lambda: {
            "justjoin": JobSearchSourceConfig(enabled=True),
            "rocketjobs": JobSearchSourceConfig(enabled=True),
            "nofluffjobs": JobSearchSourceConfig(enabled=True),
            "pracuj": JobSearchSourceConfig(enabled=True),
            "linkedin": JobSearchSourceConfig(enabled=False),
        }
    )


class PlaywrightCollectionConfig(BaseModel):
    """Settings for the Playwright-based browser job collector."""

    enabled: bool = True
    headless: bool = False
    slow_mo_ms: int = 150
    max_scrolls_per_source: int = 8
    scroll_pause_ms: int = 1200
    max_urls_per_source: int = 50
    page_timeout_ms: int = 30000
    save_debug_screenshots: bool = True
    save_debug_html_preview: bool = False  # can be large; off by default
    user_agent: str | None = None


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
    form_filling: FormFillingConfig = FormFillingConfig()
    answers: AnswerGenerationConfig = AnswerGenerationConfig()
    answer_profile: AnswerProfileConfig = AnswerProfileConfig()
    answer_templates: AnswerTemplatesConfig = AnswerTemplatesConfig()
    follow_up: FollowUpConfig = FollowUpConfig()
    gmail: GmailConfig = GmailConfig()
    calendar: CalendarConfig = CalendarConfig()
    playwright: PlaywrightConfig = PlaywrightConfig()
    job_search: JobSearchConfig = JobSearchConfig()
    playwright_collection: PlaywrightCollectionConfig = PlaywrightCollectionConfig()


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
    if "form_filling" in data and isinstance(data["form_filling"], dict):
        data["form_filling"] = FormFillingConfig.model_validate(data["form_filling"])
    if "playwright" in data and isinstance(data["playwright"], dict):
        data["playwright"] = PlaywrightConfig.model_validate(data["playwright"])
    if "answers" in data and isinstance(data["answers"], dict):
        data["answers"] = AnswerGenerationConfig.model_validate(data["answers"])
    if "answer_profile" in data and isinstance(data["answer_profile"], dict):
        data["answer_profile"] = AnswerProfileConfig.model_validate(data["answer_profile"])
    if "answer_templates" in data and isinstance(data["answer_templates"], dict):
        data["answer_templates"] = AnswerTemplatesConfig.model_validate(data["answer_templates"])
    if "follow_up" in data and isinstance(data["follow_up"], dict):
        data["follow_up"] = FollowUpConfig.model_validate(data["follow_up"])
    if "gmail" in data and isinstance(data["gmail"], dict):
        data["gmail"] = GmailConfig.model_validate(data["gmail"])
    if "calendar" in data and isinstance(data["calendar"], dict):
        data["calendar"] = CalendarConfig.model_validate(data["calendar"])
    if "job_search" in data and isinstance(data["job_search"], dict):
        js = data["job_search"]
        if "sources" in js and isinstance(js["sources"], dict):
            js["sources"] = {
                k: JobSearchSourceConfig.model_validate(v) if isinstance(v, dict) else v
                for k, v in js["sources"].items()
            }
        data["job_search"] = JobSearchConfig.model_validate(js)
    if "playwright_collection" in data and isinstance(data["playwright_collection"], dict):
        data["playwright_collection"] = PlaywrightCollectionConfig.model_validate(
            data["playwright_collection"]
        )
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
