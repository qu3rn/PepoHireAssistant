"""Debug artifacts for form-filling runs.

Responsibilities
----------------
- :class:`StepLogger`     — in-memory log of filler actions (no sensitive values).
- :func:`snapshot_form`   — sanitized snapshot of visible form elements (no input values).
- :class:`FormFillDebugRecord` — aggregate metadata written to ``metadata.json``.
- :func:`save_debug_run`  — persist all artifacts under ``data/debug/form_filling/<run_id>/``.
- :func:`load_debug_runs` — list all stored debug runs ordered newest-first.
- :func:`load_debug_run`  — load a single stored debug run.

Privacy: input field *values* are never stored.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

_DEBUG_BASE = Path("data/debug/form_filling")


# ---------------------------------------------------------------------------
# Step log
# ---------------------------------------------------------------------------

_SENSITIVE_FIELDS = frozenset(
    {
        "email",
        "phone",
        "first_name",
        "last_name",
        "full_name",
        "linkedin",
        "github",
        "portfolio",
        "expected_salary",
        "availability",
        "cv_upload",
    }
)


class StepEntry(BaseModel):
    """One recorded action step."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    action: str
    target: str = ""
    status: str = "success"  # "success" | "failed" | "skipped"
    message: str = ""


class StepLogger:
    """Lightweight per-run step logger.

    Values of sensitive fields are never stored; callers must omit them.
    """

    def __init__(self) -> None:
        self._steps: list[StepEntry] = []

    def log(
        self,
        action: str,
        *,
        target: str = "",
        status: str = "success",
        message: str = "",
    ) -> None:
        self._steps.append(
            StepEntry(action=action, target=target, status=status, message=message)
        )

    def entries(self) -> list[StepEntry]:
        return list(self._steps)

    def to_dicts(self) -> list[dict[str, Any]]:
        return [
            {
                "timestamp": e.timestamp.isoformat(),
                "action": e.action,
                "target": e.target,
                "status": e.status,
                "message": e.message,
            }
            for e in self._steps
        ]


# ---------------------------------------------------------------------------
# Form snapshot (no input values)
# ---------------------------------------------------------------------------


def snapshot_form(page: Any) -> list[dict[str, str | bool]]:  # page: playwright Page
    """Return a sanitized list of visible form fields.

    Collected attributes: tag, type, name, id, placeholder, aria-label,
    label text, required, disabled.  Input *values* are intentionally omitted.
    """
    results: list[dict[str, str | bool]] = []
    try:
        # Collect <input>, <textarea>, <select> elements that are not hidden
        elements = page.query_selector_all(
            "input:not([type=hidden]), textarea, select"
        )
        for el in elements:
            try:
                if not el.is_visible():
                    continue
                tag = (el.evaluate("e => e.tagName") or "").lower()
                input_type = (el.get_attribute("type") or "").lower()
                name = el.get_attribute("name") or ""
                el_id = el.get_attribute("id") or ""
                placeholder = el.get_attribute("placeholder") or ""
                aria_label = el.get_attribute("aria-label") or ""
                required = el.get_attribute("required") is not None
                disabled = el.get_attribute("disabled") is not None

                # Try to find an associated <label>
                label_text = ""
                if el_id:
                    try:
                        label_el = page.query_selector(f'label[for="{el_id}"]')
                        if label_el and label_el.is_visible():
                            label_text = (label_el.inner_text(timeout=500) or "").strip()
                    except Exception:  # noqa: BLE001
                        pass

                # Fallback: aria-labelledby
                if not label_text:
                    labelledby = el.get_attribute("aria-labelledby") or ""
                    if labelledby:
                        try:
                            lbl = page.query_selector(f"#{labelledby.split()[0]}")
                            if lbl:
                                label_text = (lbl.inner_text(timeout=500) or "").strip()
                        except Exception:  # noqa: BLE001
                            pass

                entry: dict[str, str | bool] = {
                    "tag": tag,
                    "type": input_type,
                    "name": name,
                    "id": el_id,
                    "placeholder": placeholder,
                    "aria_label": aria_label,
                    "label": label_text,
                    "required": required,
                    "disabled": disabled,
                }
                results.append(entry)
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return results


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def detect_captcha(page: Any) -> bool:
    """Return True if page appears to contain a CAPTCHA challenge."""
    try:
        url = (page.url or "").lower()
        if any(kw in url for kw in ("captcha", "challenge", "recaptcha")):
            return True
        captcha_selectors = [
            ".g-recaptcha",
            ".h-captcha",
            'iframe[src*="recaptcha"]',
            'iframe[src*="hcaptcha"]',
            '[data-sitekey]',
            "#captcha",
            ".captcha",
        ]
        for sel in captcha_selectors:
            try:
                el = page.locator(sel)
                if el.count() and el.first.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    return False


def detect_login_wall(page: Any) -> bool:
    """Return True if the page appears to require authentication."""
    try:
        url = (page.url or "").lower()
        if any(
            k in url
            for k in ("/login", "/signin", "/sign-in", "/logowanie", "/zaloguj", "/auth/")
        ):
            return True
        try:
            if page.locator('input[type="password"]').count():
                if page.locator('input[type="password"]').first.is_visible():
                    return True
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass
    return False


def detect_blocked_page(page: Any) -> bool:
    """Return True if the page appears to be a bot-detection / blocked page."""
    try:
        title = (page.title() or "").lower()
        blocked_keywords = [
            "access denied",
            "403",
            "forbidden",
            "bot detected",
            "cloudflare",
            "attention required",
            "just a moment",
        ]
        if any(kw in title for kw in blocked_keywords):
            return True
        url = (page.url or "").lower()
        if "challenge" in url or "blocked" in url:
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


# ---------------------------------------------------------------------------
# FormFillDebugRecord
# ---------------------------------------------------------------------------


class FormFillDebugRecord(BaseModel):
    """Aggregate metadata for one form-filling debug run."""

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    offer_id: str = ""
    source: str = ""
    url: str = ""
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    filler_name: str = ""
    status: str = "failed"  # "filled" | "partial" | "failed"
    screenshot_path: str = ""
    form_snapshot_path: str = ""
    step_log_path: str = ""
    warnings: list[str] = Field(default_factory=list)
    error: str = ""
    fields_detected_summary: dict[str, int] = Field(default_factory=dict)
    fields_filled: list[str] = Field(default_factory=list)
    fields_missing: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _run_dir(run_id: str) -> Path:
    return _DEBUG_BASE / run_id


def save_debug_run(
    record: FormFillDebugRecord,
    step_log: StepLogger,
    form_snapshot: list[dict[str, str | bool]] | None,
    screenshot_bytes: bytes | None,
) -> FormFillDebugRecord:
    """Persist debug artifacts to ``data/debug/form_filling/<run_id>/``.

    Returns an updated copy of *record* with the resolved file paths.
    Files are only written when the corresponding data is not None/empty.
    All errors are suppressed so a debug failure never breaks the filler.
    """
    d = _run_dir(record.run_id)
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        return record  # directory creation failed – give up silently

    updated = record.model_copy()

    # Step log
    try:
        entries = step_log.to_dicts()
        if entries:
            path = d / "step_log.json"
            path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
            updated.step_log_path = str(path)
    except Exception:  # noqa: BLE001
        pass

    # Form snapshot
    try:
        if form_snapshot is not None:
            path = d / "form_snapshot.json"
            path.write_text(
                json.dumps(form_snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            updated.form_snapshot_path = str(path)
    except Exception:  # noqa: BLE001
        pass

    # Screenshot
    try:
        if screenshot_bytes is not None:
            path = d / "screenshot.png"
            path.write_bytes(screenshot_bytes)
            updated.screenshot_path = str(path)
    except Exception:  # noqa: BLE001
        pass

    # Metadata (written last so paths are correct)
    try:
        meta = d / "metadata.json"
        meta.write_text(
            updated.model_dump_json(indent=2), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass

    return updated


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def load_debug_runs(limit: int = 50) -> list[FormFillDebugRecord]:
    """Return up to *limit* debug runs, newest-first."""
    if not _DEBUG_BASE.exists():
        return []
    records: list[FormFillDebugRecord] = []
    for meta_path in _DEBUG_BASE.glob("*/metadata.json"):
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            records.append(FormFillDebugRecord.model_validate(data))
        except Exception:  # noqa: BLE001
            continue
    records.sort(key=lambda r: r.started_at, reverse=True)
    return records[:limit]


def load_debug_run(run_id: str) -> FormFillDebugRecord | None:
    """Load a single debug run by *run_id*."""
    meta_path = _run_dir(run_id) / "metadata.json"
    if not meta_path.exists():
        return None
    try:
        return FormFillDebugRecord.model_validate(
            json.loads(meta_path.read_text(encoding="utf-8"))
        )
    except Exception:  # noqa: BLE001
        return None


def load_step_log(run_id: str) -> list[dict[str, Any]]:
    """Load step log entries for *run_id*."""
    path = _run_dir(run_id) / "step_log.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def load_form_snapshot(run_id: str) -> list[dict[str, Any]]:
    """Load form snapshot for *run_id*."""
    path = _run_dir(run_id) / "form_snapshot.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
