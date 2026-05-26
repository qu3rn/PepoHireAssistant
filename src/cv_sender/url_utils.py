"""URL utilities: validation, normalization, and source inference."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# Query parameters that are purely tracking and carry no job-specific meaning.
# Only well-known, universally-tracking params are listed here to avoid
# accidentally stripping job-board IDs.
_TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "msclkid",
        "mc_eid",
        "ref",
        "source",
        "referrer",
    }
)

# Hostname → friendly source name
_HOSTNAME_SOURCE_MAP: dict[str, str] = {
    "rocketjobs.pl": "rocketjobs",
    "www.rocketjobs.pl": "rocketjobs",
    "pracuj.pl": "pracuj",
    "www.pracuj.pl": "pracuj",
    "linkedin.com": "linkedin",
    "www.linkedin.com": "linkedin",
    "nofluffjobs.com": "nofluffjobs",
    "www.nofluffjobs.com": "nofluffjobs",
    "justjoin.it": "justjoin",
    "www.justjoin.it": "justjoin",
    "bulldogjob.pl": "bulldogjob",
    "www.bulldogjob.pl": "bulldogjob",
    "theprotocol.it": "theprotocol",
    "www.theprotocol.it": "theprotocol",
    "indeed.com": "indeed",
    "www.indeed.com": "indeed",
    "glassdoor.com": "glassdoor",
    "www.glassdoor.com": "glassdoor",
}


def is_valid_url(url: str) -> bool:
    """Return ``True`` if *url* is a well-formed HTTP/HTTPS URL."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except ValueError:
        return False


def normalize_url(url: str) -> str:
    """Return a canonical form of *url*.

    Transformations applied (in order):
    1. Strip leading/trailing whitespace.
    2. Remove well-known tracking query parameters.
    3. Remove trailing slash from the path (unless the path is bare ``/``).

    Job-board-specific query parameters are preserved.
    """
    url = url.strip()
    try:
        parsed = urlparse(url)
    except ValueError:
        return url

    # Remove tracking params
    if parsed.query:
        qs = parse_qs(parsed.query, keep_blank_values=True)
        cleaned = {k: v for k, v in qs.items() if k.lower() not in _TRACKING_PARAMS}
        # Rebuild in sorted order for stability
        new_query = urlencode(sorted(cleaned.items()), doseq=True)
    else:
        new_query = ""

    # Strip trailing slash from path
    path = parsed.path.rstrip("/") or "/"

    normalized = urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            parsed.params,
            new_query,
            "",  # strip fragment
        )
    )
    return normalized


def infer_source(url: str) -> str:
    """Return a friendly source name derived from the URL hostname.

    Returns ``"manual"`` if the hostname is not recognised.
    """
    try:
        hostname = urlparse(url).hostname or ""
    except ValueError:
        return "manual"
    return _HOSTNAME_SOURCE_MAP.get(hostname.lower(), "manual")


def parse_url_lines(text: str) -> list[str]:
    """Split *text* into non-empty, stripped lines and return unique values.

    Preserves the first occurrence when duplicates appear within the input.
    """
    seen: set[str] = set()
    result: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped not in seen:
            seen.add(stripped)
            result.append(stripped)
    return result
