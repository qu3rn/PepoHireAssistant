"""Offer title and company normalization helpers."""

from __future__ import annotations

import html
import re
from urllib.parse import unquote_plus, urlparse

_KNOWN_TOKEN_REPLACEMENTS: dict[str, str] = {
    "ai": "AI",
    "aws": "AWS",
    "c#": "C#",
    "c++": "C++",
    "css": "CSS",
    "dotnet": ".NET",
    "gcp": "GCP",
    "graphql": "GraphQL",
    "html": "HTML",
    "javascript": "JavaScript",
    "js": "JS",
    "k8s": "K8s",
    "kubernetes": "Kubernetes",
    "nestjs": "NestJS",
    "node": "Node",
    "nodejs": "Node.js",
    "next": "Next",
    "nextjs": "Next.js",
    "php": "PHP",
    "postgres": "Postgres",
    "postgresql": "PostgreSQL",
    "python": "Python",
    "qa": "QA",
    "react": "React",
    "redis": "Redis",
    "sql": "SQL",
    "ts": "TS",
    "typescript": "TypeScript",
    "ui": "UI",
    "ux": "UX",
    "vue": "Vue",
}


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _smart_titlecase(text: str) -> str:
    words: list[str] = []
    for token in text.split(" "):
        if not token:
            continue
        cleaned = token.strip()
        lower = cleaned.lower().strip(".,;:!?()")
        replacement = _KNOWN_TOKEN_REPLACEMENTS.get(lower)
        if replacement:
            words.append(replacement)
            continue
        if "-" in cleaned and cleaned not in {"-", "--"} and not cleaned.isupper():
            parts = []
            for part in cleaned.split("-"):
                part_lower = part.lower().strip(".,;:!?()")
                part_replacement = _KNOWN_TOKEN_REPLACEMENTS.get(part_lower)
                if part_replacement:
                    parts.append(part_replacement)
                elif part:
                    parts.append(part[:1].upper() + part[1:].lower())
            words.append("-".join(parts))
            continue
        if any(ch.isdigit() for ch in cleaned) or cleaned.isupper() or any(ch in cleaned for ch in ("#", "+", "/", ".")):
            words.append(cleaned)
            continue
        if len(cleaned) > 1 and cleaned[1:].islower() and cleaned[0].isupper():
            words.append(cleaned)
            continue
        words.append(cleaned[:1].upper() + cleaned[1:].lower())
    return " ".join(words)


def _slug_to_title(raw: str) -> str:
    raw = html.unescape(raw)
    raw = unquote_plus(raw)
    raw = raw.replace("_", " ")
    raw = re.sub(r"[,/\\-]+", " ", raw)
    raw = re.sub(r"\b(?:oferta|offer|job|praca)\b\s*\d*$", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\b\d{4,}\b$", "", raw)
    raw = _collapse_whitespace(raw)
    return _smart_titlecase(raw)


def normalize_offer_title(title: str, *, source: str = "", url: str = "") -> str:
    """Return a cleaned, human-readable offer title.

    If *title* is empty, the last path segment from *url* is used as a fallback.
    Source-specific cleanup is intentionally conservative and only removes
    obvious slug fragments and platform suffixes.
    """

    raw = _collapse_whitespace(html.unescape(title or ""))
    if not raw and url:
        raw = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]

    if not raw:
        return ""

    if source.lower() == "pracuj":
        raw = re.sub(r",?\s*oferta\s*,\s*\d+$", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r",\s*\d+$", "", raw)

    raw = unquote_plus(raw)
    raw = raw.replace("+", " ")
    raw = raw.replace("_", " ")

    if re.fullmatch(r"[\w.,/-]+", raw) and (" " not in raw or raw.count("-") >= 1):
        raw = _slug_to_title(raw)
    else:
        raw = _collapse_whitespace(raw)
        raw = _smart_titlecase(raw)

    return raw[:120].strip()


def normalize_company_name(company: str) -> str:
    """Return a trimmed company name without inventing a new value."""

    return _collapse_whitespace(html.unescape(company or ""))[:120]
