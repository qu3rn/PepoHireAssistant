"""Browser session management using Playwright."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright


@contextmanager
def browser_session(headless: bool = False, slow_mo: int = 0) -> Generator[tuple[Page, Browser], None, None]:
    """Context manager that yields ``(page, browser)`` and cleans up on exit."""
    playwright: Playwright
    browser: Browser
    context: BrowserContext

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless, slow_mo=slow_mo)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="pl-PL",
        )
        page = context.new_page()
        try:
            yield page, browser
        finally:
            context.close()
            browser.close()


def navigate(page: Page, url: str, timeout: int = 30_000) -> None:
    """Navigate *page* to *url* and wait until the network is idle."""
    page.goto(url, wait_until="networkidle", timeout=timeout)
