from __future__ import annotations

import re
from datetime import UTC, datetime
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
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    selector_or_text: str = ""
    status: ModalActionStatus = "skipped"
    message: str = ""
    before_visible: bool = False
    after_visible: bool = False
    error: str = ""


class ModalHandlingResult(BaseModel):
    actions_taken: list[ModalAction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    handler_called: bool = False
    blocked_by_captcha: bool = False
    blocked_by_login: bool = False
    blocked_by_overlay: bool = False
    cookie_banner_visible_before: bool = False
    cookie_banner_visible_after: bool = False
    login_detection: dict[str, Any] = Field(default_factory=dict)


class LoginDetectionResult(BaseModel):
    navigation_login_link_detected: bool = False
    login_wall_detected: bool = False
    login_redirect_detected: bool = False
    login_form_detected: bool = False
    reason: str = ""
    detected_texts: list[str] = Field(default_factory=list)
    current_url: str = ""
    useful_content_detected: bool = False


class PlaywrightModalSettings(BaseModel):
    enabled: bool = True
    cookie_mode: CookieMode = "reject_optional"
    close_newsletters: bool = True
    close_generic_overlays: bool = True
    max_attempts: int = 4
    timeout_ms: int = 5000
    retry_delay_ms: int = 750
    screenshot_after_handling: bool = True


_REJECT_TEXTS = [
    "odrzu",
    "odrzuć",
    "odrzuć wszystko",
    "tylko niezbędne",
    "niezbędne",
    "zapisz wybór",
    "zapisz moje wybory",
    "zapisz preferencje",
    "nie zgadzam",
    "kontynuuj bez zgody",
    "reject",
    "reject all",
    "necessary only",
    "only necessary",
    "save choices",
    "save preferences",
    "decline",
    "continue without accepting",
]

_ACCEPT_TEXTS = [
    "akceptuję",
    "akceptuj",
    "akceptuj wszystko",
    "akceptuj wszystkie",
    "zgadzam się",
    "przejdź do serwisu",
    "accept",
    "accept all",
    "i agree",
    "agree",
    "allow all",
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
_LOGIN_WALL_PHRASES = [
    "zaloguj się, aby kontynuować",
    "zaloguj sie, aby kontynuowac",
    "log in to continue",
    "sign in to continue",
    "musisz się zalogować",
    "musisz sie zalogowac",
    "login required",
    "authentication required",
]
_LOGIN_URL_TOKENS = ["/login", "/logowanie", "/signin", "/sign-in", "/account/login", "/auth"]
_LOGIN_SUBMIT_TOKENS = ["zaloguj", "login", "log in", "sign in", "continue", "kontynuuj"]

_DANGEROUS_CLICK_TOKENS = [
    "apply",
    "aplikuj",
    "submit",
    "wyślij",
    "send",
    "zarejestruj",
    "register",
    "sign up",
    "login",
    "log in",
    "zaloguj",
    "utwórz konto",
    "create account",
]

_COOKIE_TEXT_TOKENS = [
    "cookies",
    "cookie",
    "ciasteczka",
    "pliki cookie",
    "zgoda",
    "consent",
    "privacy",
    "prywatności",
]

_COOKIE_SELECTOR_SNIPPETS = [
    "[id*='cookie' i]",
    "[class*='cookie' i]",
    "[id*='consent' i]",
    "[class*='consent' i]",
    "[id*='cmp' i]",
    "[class*='cmp' i]",
    "[id*='onetrust' i]",
    "[class*='onetrust' i]",
    "[id*='didomi' i]",
    "[class*='didomi' i]",
    "[id*='usercentrics' i]",
    "[class*='usercentrics' i]",
    "[id*='cookiebot' i]",
    "[class*='cookiebot' i]",
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
            before_visible = detect_cookie_banner_visible(page)
            ok, msg = _safe_click(item)
            after_visible = before_visible
            try:
                after_visible = detect_cookie_banner_visible(page)
            except Exception:  # noqa: BLE001
                after_visible = before_visible
            return ModalAction(
                type=action_type,
                selector_or_text=label,
                status="success" if ok else "failed",
                message="clicked" if ok else msg,
                before_visible=before_visible,
                after_visible=after_visible,
                error="" if ok else msg,
            )
    return None


def detect_cookie_banner_visible(page: Any) -> bool:
    page_text = ""
    try:
        page_text = page.inner_text("body") or ""
    except Exception:  # noqa: BLE001
        page_text = ""

    if _contains_any(page_text, _COOKIE_TEXT_TOKENS):
        return True

    script = """
    (selectors) => {
      const isVisible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        if (!style || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0) {
          return false;
        }
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      };
      return selectors.some((selector) => {
        try {
          return Array.from(document.querySelectorAll(selector)).some(isVisible);
        } catch {
          return false;
        }
      });
    }
    """
    try:
        return bool(page.evaluate(script, _COOKIE_SELECTOR_SNIPPETS))
    except Exception:  # noqa: BLE001
        return False


def _detect_blockers(page: Any) -> tuple[bool, bool, str]:
    page_text = ""
    try:
        page_text = page.inner_text("body") or ""
    except Exception:  # noqa: BLE001
        pass

    captcha = _contains_any(page_text, _CAPTCHA_TOKENS)

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

    login_detection = detect_login_detection(page)

    return captcha, login_detection.login_wall_detected, page_text


def _safe_is_visible(locator: Any) -> bool:
    try:
        return bool(locator.is_visible())
    except Exception:  # noqa: BLE001
        return False


def _any_visible(page: Any, selector: str) -> bool:
    try:
        loc = page.locator(selector)
        count = loc.count()
        if not isinstance(count, int):
            return False
        for idx in range(min(count, 20)):
            if _safe_is_visible(loc.nth(idx)):
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _first_visible_text(page: Any, selector: str) -> str:
    try:
        loc = page.locator(selector)
        count = loc.count()
        if not isinstance(count, int):
            return ""
        for idx in range(min(count, 20)):
            item = loc.nth(idx)
            if _safe_is_visible(item):
                text = _extract_label_from_locator(item)
                if text:
                    return text
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _detect_useful_content(page: Any, body_text: str) -> bool:
    try:
        result = page.evaluate(
            """
            () => {
              const isVisible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (!style || style.display === 'none' || style.visibility === 'hidden') return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
              };
              const offerLinks = Array.from(document.querySelectorAll('a[href]')).filter((a) => {
                if (!isVisible(a)) return false;
                const href = (a.getAttribute('href') || '').toLowerCase();
                return /job-offer|oferta-pracy|,oferta,|\\/job\\/|\\/praca\\//.test(href);
              }).length;
              const jobCards = Array.from(document.querySelectorAll('article, [data-testid*="offer" i], [class*="job" i], [class*="offer" i], [class*="listing" i]')).filter(isVisible).length;
              const listingContainers = Array.from(document.querySelectorAll('[data-testid*="list" i], [class*="results" i], [class*="jobs" i], [class*="listing" i]')).filter(isVisible).length;
              const body = (document.body?.innerText || '').trim();
              return {
                offerLinks,
                jobCards,
                listingContainers,
                bodyLength: body.length,
              };
            }
            """
        )
        if isinstance(result, dict):
            if int(result.get("offerLinks") or 0) > 0:
                return True
            if int(result.get("jobCards") or 0) > 2:
                return True
            if int(result.get("listingContainers") or 0) > 0:
                return True
            if int(result.get("bodyLength") or 0) > 800:
                return True
    except Exception:  # noqa: BLE001
        pass

    low = _norm(body_text)
    non_login_tokens = ["react", "frontend", "developer", "oferta", "praca", "jobs", "remote", "salary", "wynagrodzenie"]
    return len(low) > 800 and any(token in low for token in non_login_tokens)


def _detect_navigation_login_links(page: Any, body_text: str) -> tuple[bool, list[str]]:
    detected: list[str] = []
    try:
        rows = page.evaluate(
            """
            () => {
              const isVisible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (!style || style.display === 'none' || style.visibility === 'hidden') return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
              };
              const inNav = (el) => Boolean(el.closest('header, nav, [role="navigation"], footer'));
              const out = [];
              for (const el of Array.from(document.querySelectorAll('a, button'))) {
                if (!isVisible(el)) continue;
                if (!inNav(el)) continue;
                const text = ((el.innerText || el.textContent || '') + ' ' + (el.getAttribute('aria-label') || '')).trim();
                if (text) out.push(text);
              }
              return out.slice(0, 60);
            }
            """
        )
        if isinstance(rows, list):
            for row in rows:
                label = _norm(str(row))
                if _contains_any(label, _LOGIN_TOKENS):
                    detected.append(label)
    except Exception:  # noqa: BLE001
        pass

    if not detected and _contains_any(body_text, _LOGIN_TOKENS):
        # Fallback hint only when DOM introspection is unavailable.
        detected.append("login text detected")

    return bool(detected), list(dict.fromkeys(detected))


def detect_login_detection(page: Any, original_listing_url: str = "") -> LoginDetectionResult:
    """Detect real login walls while avoiding nav/header login false positives."""
    result = LoginDetectionResult()
    body_text = ""
    try:
        body_text = page.inner_text("body") or ""
    except Exception:  # noqa: BLE001
        body_text = ""

    current_url = ""
    try:
        current_url = str(getattr(page, "url", "") or "")
    except Exception:  # noqa: BLE001
        current_url = ""
    result.current_url = current_url

    result.useful_content_detected = _detect_useful_content(page, body_text)

    nav_login, nav_texts = _detect_navigation_login_links(page, body_text)
    result.navigation_login_link_detected = nav_login
    result.detected_texts.extend(nav_texts[:8])

    cur_low = current_url.lower()
    orig_low = (original_listing_url or "").lower()
    current_login_url = any(token in cur_low for token in _LOGIN_URL_TOKENS)
    original_login_url = any(token in orig_low for token in _LOGIN_URL_TOKENS)
    result.login_redirect_detected = bool(current_login_url and not original_login_url)

    has_visible_password = _any_visible(page, "input[type='password']")
    has_visible_login_input = (
        _any_visible(page, "input[type='email']")
        or _any_visible(page, "input[name*='email' i]")
        or _any_visible(page, "input[name*='login' i]")
        or _any_visible(page, "input[autocomplete='username']")
        or _any_visible(page, "input[name*='user' i]")
    )
    submit_text = _first_visible_text(page, "button[type='submit'], input[type='submit'], button")
    has_login_submit = _contains_any(submit_text, _LOGIN_SUBMIT_TOKENS)
    form_condition = has_visible_password and has_visible_login_input and has_login_submit
    result.login_form_detected = bool(form_condition)

    overlay_form_detected = False
    try:
        overlay_form_detected = bool(
            page.evaluate(
                """
                () => {
                  const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    if (!style || style.display === 'none' || style.visibility === 'hidden') return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                  };
                  const vw = Math.max(window.innerWidth || 0, 1);
                  const vh = Math.max(window.innerHeight || 0, 1);
                  const nodes = Array.from(document.querySelectorAll('div,section,aside,dialog,[role="dialog"]'));
                  for (const node of nodes) {
                    if (!isVisible(node)) continue;
                    const style = window.getComputedStyle(node);
                    if (!(style.position === 'fixed' || style.position === 'sticky' || node.getAttribute('role') === 'dialog')) continue;
                    const rect = node.getBoundingClientRect();
                    const areaRatio = (rect.width * rect.height) / (vw * vh);
                    if (areaRatio < 0.35) continue;
                    const password = node.querySelector('input[type="password"]');
                    const user = node.querySelector('input[type="email"], input[name*="email" i], input[name*="login" i], input[autocomplete="username"]');
                    const submit = node.querySelector('button[type="submit"], input[type="submit"], button');
                    if (password && user && submit && isVisible(password) && isVisible(user) && isVisible(submit)) return true;
                  }
                  return false;
                }
                """
            )
        )
    except Exception:  # noqa: BLE001
        overlay_form_detected = False

    if overlay_form_detected:
        result.login_form_detected = True

    low_body = _norm(body_text)
    blocking_phrases = [phrase for phrase in _LOGIN_WALL_PHRASES if phrase in low_body]
    if blocking_phrases:
        result.detected_texts.extend(blocking_phrases[:4])

    content_blocking = bool(blocking_phrases) and not result.useful_content_detected

    strong_reasons: list[str] = []
    if result.login_redirect_detected:
        strong_reasons.append("redirected_to_login_url")
    if form_condition:
        strong_reasons.append("visible_login_form_detected")
    if overlay_form_detected:
        strong_reasons.append("blocking_login_overlay_detected")
    if content_blocking:
        strong_reasons.append("login_required_text_and_no_useful_content")

    result.login_wall_detected = bool(strong_reasons)

    if result.login_wall_detected:
        result.reason = ", ".join(strong_reasons)
    elif result.navigation_login_link_detected and result.useful_content_detected:
        result.reason = "Login link detected in navigation, but page content is accessible."
    elif result.navigation_login_link_detected:
        result.reason = "Navigation login link detected without strong login-wall signals."
    else:
        result.reason = "No login wall signals detected."

    result.detected_texts = list(dict.fromkeys(result.detected_texts))[:12]
    return result


def _resolve_modal_settings(settings: Any | None) -> PlaywrightModalSettings:
    if settings is None:
        return PlaywrightModalSettings()

    # Already narrowed to modal settings model/dict
    if isinstance(settings, dict):
        return PlaywrightModalSettings.model_validate(settings)

    if hasattr(settings, "modals"):
        modals = getattr(settings, "modals")
        if hasattr(modals, "model_dump"):
            return PlaywrightModalSettings.model_validate(modals.model_dump())
        return PlaywrightModalSettings.model_validate(modals)

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
    result = ModalHandlingResult(handler_called=True)
    modal_cfg = _resolve_modal_settings(settings)
    result.cookie_banner_visible_before = detect_cookie_banner_visible(page)
    result.cookie_banner_visible_after = result.cookie_banner_visible_before

    if not modal_cfg.enabled or modal_cfg.cookie_mode == "disabled":
        result.actions_taken.append(
            ModalAction(
                type="none",
                selector_or_text="modal_handling_disabled",
                status="skipped",
                message="disabled",
                before_visible=result.cookie_banner_visible_before,
                after_visible=result.cookie_banner_visible_before,
            )
        )
        return result

    for attempt_idx in range(max(1, modal_cfg.max_attempts)):
        captcha, _login_wall, page_text = _detect_blockers(page)
        login_detection = detect_login_detection(page)
        result.login_detection = login_detection.model_dump(mode="json")
        if captcha:
            result.blocked_by_captcha = True
            result.warnings.append("CAPTCHA detected; no bypass attempted.")
            result.actions_taken.append(
                ModalAction(
                    type="captcha_detected",
                    selector_or_text="captcha",
                    status="skipped",
                    message="detected",
                    before_visible=result.cookie_banner_visible_after,
                    after_visible=result.cookie_banner_visible_after,
                )
            )
            return result
        if login_detection.navigation_login_link_detected and not login_detection.login_wall_detected:
            msg = "Login link detected in navigation, but page content is accessible."
            if msg not in result.warnings:
                result.warnings.append(msg)
        if login_detection.login_wall_detected:
            result.blocked_by_login = True
            result.warnings.append("Login wall detected; no bypass attempted.")
            result.actions_taken.append(
                ModalAction(
                    type="login_detected",
                    selector_or_text=login_detection.reason or "login",
                    status="skipped",
                    message="detected",
                    before_visible=result.cookie_banner_visible_after,
                    after_visible=result.cookie_banner_visible_after,
                )
            )
            return result

        attempt_actions: list[ModalAction] = []
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
            attempt_actions.append(action)
            if action.status == "success":
                try:
                    page.wait_for_timeout(min(max(200, modal_cfg.retry_delay_ms), modal_cfg.timeout_ms))
                except Exception:  # noqa: BLE001
                    pass

        # Newsletter overlays
        if modal_cfg.close_newsletters and _contains_any(page_text, _NEWSLETTER_TOKENS):
            nl = _click_by_tokens(page, _CLOSE_TEXTS, "newsletter_close")
            if nl is not None:
                attempt_actions.append(nl)

        # Generic non-protected overlays
        if modal_cfg.close_generic_overlays:
            overlay = _click_by_tokens(page, _CLOSE_TEXTS, "overlay_close")
            if overlay is not None:
                attempt_actions.append(overlay)

        result.actions_taken.extend(attempt_actions)
        result.cookie_banner_visible_after = detect_cookie_banner_visible(page)

        if not result.cookie_banner_visible_after and any(a.status == "success" for a in attempt_actions):
            break

        if not attempt_actions and not result.cookie_banner_visible_after:
            break

        if attempt_idx >= modal_cfg.max_attempts - 1:
            break

        try:
            page.wait_for_timeout(min(max(150, modal_cfg.retry_delay_ms), modal_cfg.timeout_ms))
        except Exception:  # noqa: BLE001
            pass

        if not attempt_actions and result.cookie_banner_visible_after:
            continue
        if not attempt_actions and not result.cookie_banner_visible_after:
            break

    if not result.actions_taken:
        result.actions_taken.append(
            ModalAction(
                type="none",
                selector_or_text="no_modal_action",
                status="skipped",
                message=context,
                before_visible=result.cookie_banner_visible_before,
                after_visible=result.cookie_banner_visible_after,
            )
        )

    # Heuristic: if obvious overlay words remain and no successful action happened
    if any(a.status == "failed" for a in result.actions_taken):
        result.blocked_by_overlay = True
        result.warnings.append("Some overlays may still block interactions.")

    if result.cookie_banner_visible_after:
        result.warnings.append("Cookie banner still visible after modal handler.")

    return result
