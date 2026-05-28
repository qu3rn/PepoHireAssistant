from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field


ModalActionType = Literal[
    "cookie_accept",
    "cookie_reject",
    "cookie_close",
    "newsletter_close",
    "overlay_close",
    "captcha_detected",
    "login_detected",
    "none",
]
ModalActionStatus = Literal["success", "failed", "skipped"]
CookieMode = Literal["accept_all", "reject_optional", "close_only", "disabled"]


class ModalAction(BaseModel):
    type: ModalActionType
    selector_or_text: str = ""
    status: ModalActionStatus = "skipped"
    message: str = ""


class ModalHandlingResult(BaseModel):
    actions_taken: list[ModalAction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocked_by_captcha: bool = False
    blocked_by_login: bool = False
    blocked_by_overlay: bool = False


class PlaywrightModalSettings(BaseModel):
    enabled: bool = True
    cookie_mode: CookieMode = "reject_optional"
    close_newsletters: bool = True
    close_generic_overlays: bool = True
    max_attempts: int = 3
    timeout_ms: int = 3000


_REJECT_TEXTS = [
    "odrzu",
    "odrzuć",
    "odrzuć wszystko",
    "tylko niezbędne",
    "zapisz preferencje",
    "nie zgadzam",
    "reject",
    "reject all",
    "necessary only",
    "only necessary",
    "save preferences",
    "decline",
]

_ACCEPT_TEXTS = [
    "akceptuję",
    "akceptuj",
    "akceptuj wszystko",
    "zgadzam się",
    "accept",
    "accept all",
    "i agree",
    "agree",
]

_CLOSE_TEXTS = ["zamknij", "close", "x", "×"]

_NEWSLETTER_TOKENS = [
    "newsletter",
    "powiadomienia",
    "subskrybuj",
    "subscribe",
    "zapisz się",
    "sign up",
    "promocje",
]

_CAPTCHA_TOKENS = ["captcha", "recaptcha", "hcaptcha", "i'm not a robot", "nie jestem robotem"]
_LOGIN_TOKENS = ["zaloguj", "log in", "login", "sign in", "utwórz konto", "create account"]

_DANGEROUS_CLICK_TOKENS = [
    "apply",
    "aplikuj",
    "submit",
    "wyślij",
    "zarejestruj",
    "register",
    "join",
    "sign up",
    "utwórz konto",
    "create account",
]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _contains_any(text: str, tokens: list[str]) -> bool:
    low = _norm(text)
    return any(tok in low for tok in tokens)


def _extract_label_from_locator(locator: Any) -> str:
    parts: list[str] = []
    for fn in ("inner_text", "text_content"):
        try:
            value = getattr(locator, fn)()
            if value:
                parts.append(str(value))
        except Exception:  # noqa: BLE001
            pass
    for attr in ("aria-label", "title", "name"):
        try:
            value = locator.get_attribute(attr)
            if value:
                parts.append(str(value))
        except Exception:  # noqa: BLE001
            pass
    return _norm(" ".join(parts))


def _safe_click(locator: Any) -> tuple[bool, str]:
    label = _extract_label_from_locator(locator)
    if _contains_any(label, _DANGEROUS_CLICK_TOKENS):
        return False, f"blocked dangerous action: {label}"
    try:
        locator.click(timeout=1200)
        return True, label
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _candidate_clickables(page: Any) -> list[Any]:
    candidates: list[Any] = []
    for sel in (
        "button",
        "[role='button']",
        "a[role='button']",
        "[aria-label*='close' i]",
        "[aria-label*='zamknij' i]",
        "[title*='close' i]",
        "[title*='zamknij' i]",
        "[data-testid*='close' i]",
    ):
        try:
            loc = page.locator(sel)
            count = min(loc.count(), 20)
            for i in range(count):
                item = loc.nth(i)
                try:
                    if item.is_visible():
                        candidates.append(item)
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            continue
    return candidates


def _click_by_tokens(page: Any, tokens: list[str], action_type: ModalActionType) -> ModalAction | None:
    for item in _candidate_clickables(page):
        label = _extract_label_from_locator(item)
        if not label:
            continue
        if _contains_any(label, tokens):
            ok, msg = _safe_click(item)
            return ModalAction(
                type=action_type,
                selector_or_text=label,
                status="success" if ok else "failed",
                message="clicked" if ok else msg,
            )
    return None


def _detect_blockers(page: Any) -> tuple[bool, bool, str]:
    page_text = ""
    try:
        page_text = page.inner_text("body") or ""
    except Exception:  # noqa: BLE001
        pass

    captcha = _contains_any(page_text, _CAPTCHA_TOKENS)
    login = _contains_any(page_text, _LOGIN_TOKENS)

    for sel in (
        ".g-recaptcha",
        ".h-captcha",
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
    ):
        try:
            loc = page.locator(sel)
            count = loc.count()
            if not isinstance(count, int):
                count = 0
            visible = False
            if count > 0:
                try:
                    visible = bool(loc.first.is_visible())
                except Exception:  # noqa: BLE001
                    visible = False
            if count > 0 and visible:
                captcha = True
                break
        except Exception:  # noqa: BLE001
            continue

    try:
        pw = page.locator("input[type='password']")
        pw_count = pw.count()
        if not isinstance(pw_count, int):
            pw_count = 0
        pw_visible = False
        if pw_count > 0:
            try:
                pw_visible = bool(pw.first.is_visible())
            except Exception:  # noqa: BLE001
                pw_visible = False
        if pw_count > 0 and pw_visible:
            login = True
    except Exception:  # noqa: BLE001
        pass

    return captcha, login, page_text


def _resolve_modal_settings(settings: Any | None) -> PlaywrightModalSettings:
    if settings is None:
        return PlaywrightModalSettings()

    # Already narrowed to modal settings model/dict
    if isinstance(settings, dict):
        return PlaywrightModalSettings.model_validate(settings)

    if hasattr(settings, "modals"):
        return PlaywrightModalSettings.model_validate(getattr(settings, "modals"))

    if hasattr(settings, "playwright") and hasattr(settings.playwright, "modals"):
        modals = settings.playwright.modals
        if hasattr(modals, "model_dump"):
            return PlaywrightModalSettings.model_validate(modals.model_dump())
        return PlaywrightModalSettings.model_validate(modals)

    if hasattr(settings, "model_dump"):
        try:
            return PlaywrightModalSettings.model_validate(settings.model_dump())
        except Exception:  # noqa: BLE001
            pass

    return PlaywrightModalSettings.model_validate(settings)


def handle_common_modals(page: Any, settings: Any = None, context: str = "collection") -> ModalHandlingResult:
    """Handle harmless cookie/newsletter overlays without bypassing protected pages."""
    result = ModalHandlingResult()
    modal_cfg = _resolve_modal_settings(settings)

    if not modal_cfg.enabled or modal_cfg.cookie_mode == "disabled":
        result.actions_taken.append(
            ModalAction(type="none", selector_or_text="modal_handling_disabled", status="skipped", message="disabled")
        )
        return result

    for _ in range(max(1, modal_cfg.max_attempts)):
        captcha, login, page_text = _detect_blockers(page)
        if captcha:
            result.blocked_by_captcha = True
            result.warnings.append("CAPTCHA detected; no bypass attempted.")
            result.actions_taken.append(
                ModalAction(type="captcha_detected", selector_or_text="captcha", status="skipped", message="detected")
            )
            return result
        if login:
            result.blocked_by_login = True
            result.warnings.append("Login wall detected; no bypass attempted.")
            result.actions_taken.append(
                ModalAction(type="login_detected", selector_or_text="login", status="skipped", message="detected")
            )
            return result

        action: ModalAction | None = None

        if modal_cfg.cookie_mode == "reject_optional":
            action = _click_by_tokens(page, _REJECT_TEXTS, "cookie_reject")
            if action is None:
                action = _click_by_tokens(page, _CLOSE_TEXTS, "cookie_close")
        elif modal_cfg.cookie_mode == "accept_all":
            action = _click_by_tokens(page, _ACCEPT_TEXTS, "cookie_accept")
            if action is None:
                action = _click_by_tokens(page, _CLOSE_TEXTS, "cookie_close")
        elif modal_cfg.cookie_mode == "close_only":
            action = _click_by_tokens(page, _CLOSE_TEXTS, "cookie_close")

        if action is not None:
            result.actions_taken.append(action)
            if action.status == "success":
                try:
                    page.wait_for_timeout(min(max(200, modal_cfg.timeout_ms // 4), 900))
                except Exception:  # noqa: BLE001
                    pass

        # Newsletter overlays
        if modal_cfg.close_newsletters and _contains_any(page_text, _NEWSLETTER_TOKENS):
            nl = _click_by_tokens(page, _CLOSE_TEXTS, "newsletter_close")
            if nl is not None:
                result.actions_taken.append(nl)

        # Generic non-protected overlays
        if modal_cfg.close_generic_overlays:
            overlay = _click_by_tokens(page, _CLOSE_TEXTS, "overlay_close")
            if overlay is not None:
                result.actions_taken.append(overlay)

        # If nothing was actionable, stop attempts.
        if not result.actions_taken or all(a.status != "success" for a in result.actions_taken[-2:]):
            break

    if not result.actions_taken:
        result.actions_taken.append(ModalAction(type="none", selector_or_text="no_modal_action", status="skipped", message=context))

    # Heuristic: if obvious overlay words remain and no successful action happened
    if any(a.status == "failed" for a in result.actions_taken):
        result.blocked_by_overlay = True
        result.warnings.append("Some overlays may still block interactions.")

    return result
