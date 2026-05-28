from __future__ import annotations

from cv_sender.playwright_helpers import handle_common_modals


class _FakeItem:
    def __init__(self, text: str = "", attrs: dict[str, str] | None = None) -> None:
        self.text = text
        self.attrs = attrs or {}
        self.clicked = False

    def is_visible(self) -> bool:
        return True

    def inner_text(self) -> str:
        return self.text

    def text_content(self) -> str:
        return self.text

    def get_attribute(self, name: str):
        return self.attrs.get(name)

    def click(self, timeout: int = 1200) -> None:
        self.clicked = True


class _FakeLocator:
    def __init__(self, items: list[_FakeItem]) -> None:
        self.items = items

    def count(self) -> int:
        return len(self.items)

    def nth(self, idx: int) -> _FakeItem:
        return self.items[idx]

    @property
    def first(self) -> _FakeItem:
        return self.items[0]


class _FakePage:
    def __init__(self, body_text: str, clickables: list[_FakeItem]) -> None:
        self.body_text = body_text
        self.clickables = clickables

    def inner_text(self, selector: str) -> str:
        assert selector == "body"
        return self.body_text

    def locator(self, selector: str) -> _FakeLocator:
        # For blocker selectors return empty by default.
        if selector in (
            ".g-recaptcha",
            ".h-captcha",
            "iframe[src*='recaptcha']",
            "iframe[src*='hcaptcha']",
            "input[type='password']",
        ):
            return _FakeLocator([])
        return _FakeLocator(self.clickables)

    def wait_for_timeout(self, timeout: int) -> None:
        _ = timeout


def test_reject_optional_prefers_reject_over_accept() -> None:
    reject = _FakeItem("Reject all")
    accept = _FakeItem("Accept all")
    page = _FakePage("cookie banner", [accept, reject])

    result = handle_common_modals(page, {"cookie_mode": "reject_optional", "enabled": True}, context="collection")

    assert any(a.type == "cookie_reject" and a.status == "success" for a in result.actions_taken)
    assert reject.clicked is True
    assert accept.clicked is False


def test_accept_all_clicks_accept() -> None:
    accept = _FakeItem("Accept all")
    page = _FakePage("cookie banner", [accept])

    result = handle_common_modals(page, {"cookie_mode": "accept_all", "enabled": True}, context="collection")

    assert any(a.type == "cookie_accept" and a.status == "success" for a in result.actions_taken)
    assert accept.clicked is True


def test_close_only_does_not_accept_cookies() -> None:
    close = _FakeItem("Close")
    accept = _FakeItem("Accept")
    page = _FakePage("cookie banner", [accept, close])

    result = handle_common_modals(page, {"cookie_mode": "close_only", "enabled": True}, context="collection")

    assert any(a.type == "cookie_close" for a in result.actions_taken)
    assert not any(a.type == "cookie_accept" and a.status == "success" for a in result.actions_taken)


def test_disabled_does_nothing() -> None:
    reject = _FakeItem("Reject")
    page = _FakePage("cookie banner", [reject])

    result = handle_common_modals(page, {"cookie_mode": "disabled", "enabled": False}, context="collection")

    assert len(result.actions_taken) == 1
    assert result.actions_taken[0].type == "none"
    assert reject.clicked is False


def test_newsletter_close_uses_close_button_only() -> None:
    close = _FakeItem("Zamknij")
    signup = _FakeItem("Sign up")
    page = _FakePage("newsletter subscribe zapisz się", [signup, close])

    result = handle_common_modals(
        page,
        {
            "enabled": True,
            "cookie_mode": "close_only",
            "close_newsletters": True,
            "close_generic_overlays": False,
        },
        context="collection",
    )

    assert any(a.type in ("newsletter_close", "cookie_close") and a.status == "success" for a in result.actions_taken)
    assert close.clicked is True
    assert signup.clicked is False


def test_does_not_click_apply_submit_register_buttons() -> None:
    dangerous = _FakeItem("Close and submit")
    page = _FakePage("overlay", [dangerous])

    result = handle_common_modals(page, {"enabled": True, "cookie_mode": "close_only"}, context="collection")

    assert dangerous.clicked is False
    assert any(a.status in ("failed", "skipped") for a in result.actions_taken)


def test_does_not_click_explicit_dangerous_button_texts() -> None:
    dangerous_items = [
        _FakeItem("Apply"),
        _FakeItem("Aplikuj"),
        _FakeItem("Submit"),
        _FakeItem("Wyślij"),
        _FakeItem("Send"),
        _FakeItem("Register"),
        _FakeItem("Zarejestruj"),
        _FakeItem("Sign up"),
        _FakeItem("Login"),
        _FakeItem("Log in"),
        _FakeItem("Zaloguj"),
        _FakeItem("Utwórz konto"),
    ]
    page = _FakePage("cookies overlay", dangerous_items)

    result = handle_common_modals(page, {"enabled": True, "cookie_mode": "close_only"}, context="collection")

    assert all(item.clicked is False for item in dangerous_items)
    assert any(action.type == "none" for action in result.actions_taken)


def test_captcha_detection_sets_flag() -> None:
    page = _FakePage("Please solve captcha - I am not a robot", [])

    result = handle_common_modals(page, {"enabled": True, "cookie_mode": "reject_optional"}, context="collection")

    assert result.blocked_by_captcha is True
    assert any(a.type == "captcha_detected" for a in result.actions_taken)


def test_login_detection_sets_flag() -> None:
    page = _FakePage("Sign in to continue", [])

    result = handle_common_modals(page, {"enabled": True, "cookie_mode": "reject_optional"}, context="collection")

    assert result.blocked_by_login is True
    assert any(a.type == "login_detected" for a in result.actions_taken)


def test_modal_result_is_serializable() -> None:
    page = _FakePage("cookie", [_FakeItem("Reject all")])
    result = handle_common_modals(page, {"enabled": True, "cookie_mode": "reject_optional"}, context="collection")

    dumped = result.model_dump(mode="json")
    assert "actions_taken" in dumped
    assert isinstance(dumped["actions_taken"], list)


def test_cookie_banner_still_visible_creates_warning() -> None:
    close = _FakeItem("Close")
    page = _FakePage("This site uses cookies and privacy settings", [close])

    result = handle_common_modals(page, {"enabled": True, "cookie_mode": "close_only", "max_attempts": 2}, context="collection")

    assert any("still visible" in warning.lower() for warning in result.warnings)
    assert result.cookie_banner_visible_before is True
    assert result.cookie_banner_visible_after is True
