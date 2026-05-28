from __future__ import annotations

from cv_sender.playwright_helpers import detect_login_detection, handle_common_modals


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


class _LoginFakeItem(_FakeItem):
    def __init__(self, text: str = "", attrs: dict[str, str] | None = None, visible: bool = True) -> None:
        super().__init__(text=text, attrs=attrs)
        self._visible = visible

    def is_visible(self) -> bool:
        return self._visible


class _LoginFakePage:
    def __init__(
        self,
        *,
        url: str,
        body_text: str,
        selector_map: dict[str, list[_LoginFakeItem]] | None = None,
        nav_texts: list[str] | None = None,
        useful_content: dict[str, int] | None = None,
        overlay_login_form: bool = False,
    ) -> None:
        self.url = url
        self._body_text = body_text
        self._selector_map = selector_map or {}
        self._nav_texts = nav_texts or []
        self._useful_content = useful_content or {"offerLinks": 0, "jobCards": 0, "listingContainers": 0, "bodyLength": len(body_text)}
        self._overlay_login_form = overlay_login_form

    def inner_text(self, selector: str) -> str:
        assert selector == "body"
        return self._body_text

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self._selector_map.get(selector, []))

    def evaluate(self, script: str, _arg=None):
        if "header, nav" in script:
            return list(self._nav_texts)
        if "offerLinks" in script and "jobCards" in script:
            return dict(self._useful_content)
        if "areaRatio" in script and "input[type=\"password\"]" in script:
            return self._overlay_login_form
        return False


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


def test_navigation_zaloguj_link_only_is_not_login_wall() -> None:
    page = _LoginFakePage(
        url="https://example.com/jobs",
        body_text="Oferty pracy frontend React",
        nav_texts=["Zaloguj", "Konto"],
        useful_content={"offerLinks": 10, "jobCards": 5, "listingContainers": 1, "bodyLength": 1200},
    )
    result = detect_login_detection(page, original_listing_url="https://example.com/jobs")
    assert result.navigation_login_link_detected is True
    assert result.login_wall_detected is False


def test_navigation_login_link_only_is_not_login_wall() -> None:
    page = _LoginFakePage(
        url="https://example.com/jobs",
        body_text="Senior React jobs",
        nav_texts=["Login", "Sign in"],
        useful_content={"offerLinks": 6, "jobCards": 3, "listingContainers": 1, "bodyLength": 1000},
    )
    result = detect_login_detection(page, original_listing_url="https://example.com/jobs")
    assert result.navigation_login_link_detected is True
    assert result.login_wall_detected is False


def test_redirect_to_login_url_detected_as_login_wall() -> None:
    page = _LoginFakePage(
        url="https://example.com/login",
        body_text="Please sign in",
    )
    result = detect_login_detection(page, original_listing_url="https://example.com/jobs")
    assert result.login_redirect_detected is True
    assert result.login_wall_detected is True


def test_visible_email_password_and_submit_detected_as_login_wall() -> None:
    page = _LoginFakePage(
        url="https://example.com/jobs",
        body_text="Welcome back",
        selector_map={
            "input[type='password']": [_LoginFakeItem(visible=True)],
            "input[type='email']": [_LoginFakeItem(visible=True)],
            "button[type='submit'], input[type='submit'], button": [_LoginFakeItem(text="Sign in", visible=True)],
        },
    )
    result = detect_login_detection(page, original_listing_url="https://example.com/jobs")
    assert result.login_form_detected is True
    assert result.login_wall_detected is True


def test_login_phrase_without_content_detected_as_login_wall() -> None:
    page = _LoginFakePage(
        url="https://example.com/jobs",
        body_text="Log in to continue",
        useful_content={"offerLinks": 0, "jobCards": 0, "listingContainers": 0, "bodyLength": 30},
    )
    result = detect_login_detection(page, original_listing_url="https://example.com/jobs")
    assert result.useful_content_detected is False
    assert result.login_wall_detected is True


def test_login_text_with_job_links_is_not_login_wall() -> None:
    page = _LoginFakePage(
        url="https://example.com/jobs",
        body_text="Log in to continue, or browse jobs below",
        nav_texts=["Login"],
        useful_content={"offerLinks": 12, "jobCards": 6, "listingContainers": 1, "bodyLength": 1400},
    )
    result = detect_login_detection(page, original_listing_url="https://example.com/jobs")
    assert result.navigation_login_link_detected is True
    assert result.useful_content_detected is True
    assert result.login_wall_detected is False


def test_hidden_password_input_does_not_trigger_login_wall() -> None:
    page = _LoginFakePage(
        url="https://example.com/jobs",
        body_text="React jobs list",
        selector_map={
            "input[type='password']": [_LoginFakeItem(visible=False)],
            "input[type='email']": [_LoginFakeItem(visible=True)],
            "button[type='submit'], input[type='submit'], button": [_LoginFakeItem(text="Login", visible=True)],
        },
        useful_content={"offerLinks": 8, "jobCards": 4, "listingContainers": 1, "bodyLength": 1000},
    )
    result = detect_login_detection(page, original_listing_url="https://example.com/jobs")
    assert result.login_form_detected is False
    assert result.login_wall_detected is False


def test_useful_content_prevents_false_positive() -> None:
    page = _LoginFakePage(
        url="https://example.com/jobs",
        body_text="Authentication required in some actions, but offers are visible",
        nav_texts=["Sign in"],
        useful_content={"offerLinks": 5, "jobCards": 3, "listingContainers": 1, "bodyLength": 900},
    )
    result = detect_login_detection(page, original_listing_url="https://example.com/jobs")
    assert result.useful_content_detected is True
    assert result.login_wall_detected is False


def test_handle_modals_navigation_login_link_does_not_block() -> None:
    page = _LoginFakePage(
        url="https://example.com/jobs",
        body_text="Browse offers. Login is optional.",
        nav_texts=["Login"],
        useful_content={"offerLinks": 9, "jobCards": 5, "listingContainers": 1, "bodyLength": 1200},
    )
    result = handle_common_modals(page, {"enabled": True, "cookie_mode": "close_only"}, context="collection")
    assert result.blocked_by_login is False
    assert any("navigation" in warning.lower() for warning in result.warnings)
