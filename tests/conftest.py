"""Shared pytest fixtures applied to every test in this directory.

Prevents real HTTP requests from being made during tests by replacing
``cv_sender.extractors._fetch_html`` with a no-op that returns ``None``.

Tests that need to exercise extractor parsing logic should call
``extractor.extract(url, html)`` directly with fixture HTML – bypassing the
HTTP layer entirely.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def disable_http_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block all real HTTP requests to job boards during tests."""
    try:
        import cv_sender.extractors as _extractors  # noqa: PLC0415

        monkeypatch.setattr(_extractors, "_fetch_html", lambda _url: None)
    except ImportError:
        pass
