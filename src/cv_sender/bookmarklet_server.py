"""Tiny local HTTP server for the "Save to Job Assistant" bookmarklet.

Start with:
    cv-sender bookmarklet-server
    # or directly:
    uvicorn cv_sender.bookmarklet_server:app --host 127.0.0.1 --port 8765

The server binds to 127.0.0.1 only and is never reachable from outside
the local machine.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse

from cv_sender import services
from cv_sender.models import ImportStatus
from cv_sender.url_utils import is_valid_url

logger = logging.getLogger("cv_sender.bookmarklet")

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Default Streamlit UI URL shown in the "back" link on each response page.
STREAMLIT_URL = "http://localhost:8501"

#: JavaScript source of the bookmarklet.  Copy this verbatim as the URL of a
#: browser bookmark named "Save to Job Assistant".
BOOKMARKLET_JS = (
    "javascript:(()=>{"
    "const u=encodeURIComponent(location.href);"
    "window.open('http://localhost:8765/import?url='+u,'_blank');"
    "})()"
)

# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} – cv-sender</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 600px; margin: 40px auto; padding: 0 20px; }}
    .card {{ border-radius: 8px; padding: 20px 24px; margin-top: 20px; }}
    .success   {{ background: #d4edda; border: 1px solid #28a745; }}
    .duplicate {{ background: #fff3cd; border: 1px solid #ffc107; }}
    .error     {{ background: #f8d7da; border: 1px solid #dc3545; }}
    h1 {{ font-size: 1.3rem; margin-bottom: 4px; }}
    h2 {{ margin-top: 0; font-size: 1.1rem; }}
    dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; margin-top: 12px; }}
    dt {{ font-weight: 600; }}
    code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 4px; font-size: .9em; word-break: break-all; }}
    a {{ color: #0366d6; }}
    .footer {{ font-size: .85em; color: #666; margin-top: 24px; }}
  </style>
</head>
<body>
<h1>📋 cv-sender</h1>
{body}
<p class="footer"><a href="{streamlit_url}">Open cv-sender UI →</a></p>
</body>
</html>
"""


def _render(title: str, body: str) -> HTMLResponse:
    html = _HTML_TEMPLATE.format(title=title, body=body, streamlit_url=STREAMLIT_URL)
    return HTMLResponse(content=html)


def _dl_rows(pairs: list[tuple[str, str | None]]) -> str:
    """Render ``<dt>/<dd>`` pairs, skipping entries with a falsy value."""
    parts = []
    for label, value in pairs:
        if value:
            parts.append(f"<dt>{label}</dt><dd>{value}</dd>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="cv-sender bookmarklet receiver",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe – returns ``{"status": "ok"}``."""
    return {"status": "ok"}


@app.get("/import", response_class=HTMLResponse)
def import_url(
    url: str = Query(..., description="Job offer URL to import"),
) -> HTMLResponse:
    """Import a job offer from *url* and return a human-readable HTML page.

    The URL is validated locally (no HTTP request to the job page is made).
    """
    if not is_valid_url(url):
        body = (
            '<div class="card error">'
            "<h2>❌ Invalid URL</h2>"
            "<p>Not a valid HTTP/HTTPS address:</p>"
            f"<p><code>{url}</code></p>"
            "</div>"
        )
        return _render("Invalid URL", body)

    logger.info("Bookmarklet import request: %s", url)

    try:
        result = services.import_offer_from_url(url=url, auto_score=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error importing %s", url)
        body = (
            '<div class="card error">'
            "<h2>❌ Import failed</h2>"
            f"<p>An unexpected error occurred: <code>{exc!s}</code></p>"
            "</div>"
        )
        return _render("Import failed", body)

    if result.status == ImportStatus.INVALID:
        body = (
            '<div class="card error">'
            "<h2>❌ Invalid URL</h2>"
            f"<p>{result.error or 'URL validation failed.'}</p>"
            "</div>"
        )
        return _render("Invalid URL", body)

    if result.status == ImportStatus.DUPLICATE:
        rows = _dl_rows(
            [
                ("Title", result.title),
                ("Company", result.company),
                ("Score", str(result.score) if result.score is not None else None),
                ("Decision", str(result.decision) if result.decision else None),
                ("Offer ID", result.offer_id[:8] if result.offer_id else None),
            ]
        )
        body = (
            '<div class="card duplicate">'
            "<h2>⚠️ Already in database</h2>"
            "<p>This offer URL was already imported.</p>"
            f"<dl>{rows}</dl>"
            "</div>"
        )
        return _render("Duplicate", body)

    if result.status == ImportStatus.FAILED:
        body = (
            '<div class="card error">'
            "<h2>❌ Import failed</h2>"
            f"<p>{result.error or 'Unknown error.'}</p>"
            "</div>"
        )
        return _render("Import failed", body)

    # ---- ImportStatus.IMPORTED ----
    score_line = (
        f" &nbsp;·&nbsp; Score: <strong>{result.score}</strong>"
        f" &nbsp;·&nbsp; Decision: <strong>{result.decision}</strong>"
        if result.score is not None
        else ""
    )
    rows = _dl_rows(
        [
            ("Title", result.title),
            ("Company", result.company),
            ("Score", str(result.score) if result.score is not None else None),
            ("Decision", str(result.decision) if result.decision else None),
            ("Offer ID", result.offer_id[:8] if result.offer_id else None),
        ]
    )
    body = (
        '<div class="card success">'
        "<h2>✅ Offer saved</h2>"
        f"<p>Imported successfully.{score_line}</p>"
        f"<dl>{rows}</dl>"
        "</div>"
    )
    return _render("Imported", body)
