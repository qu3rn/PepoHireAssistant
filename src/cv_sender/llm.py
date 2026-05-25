"""LM Studio integration via the OpenAI-compatible API."""

from __future__ import annotations

import json
import logging
import re

from cv_sender.config import LMStudioConfig

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a job-application assistant. "
    "Evaluate job offers and return ONLY valid JSON – no markdown, no extra text."
)

_OFFER_PROMPT_TEMPLATE = """\
Evaluate the following job offer and return a JSON object in exactly this format:
{{
  "score": <integer 0-100>,
  "decision": "<apply|skip|maybe>",
  "reasons": ["<reason1>", ...],
  "risks": ["<risk1>", ...]
}}

--- JOB OFFER ---
{offer_json}

--- SCORING CRITERIA ---
{criteria_json}
"""


def build_llm_prompt(offer_data: dict, criteria_data: dict) -> str:
    """Build the prompt sent to the LLM."""
    return _OFFER_PROMPT_TEMPLATE.format(
        offer_json=json.dumps(offer_data, indent=2, default=str),
        criteria_json=json.dumps(criteria_data, indent=2, default=str),
    )


def _parse_json_response(content: str) -> dict | None:
    """Try to extract a JSON object from *content*.

    First tries a straight parse; then searches for the first ``{...}`` block.
    Returns ``None`` if no valid JSON can be found.
    """
    content = content.strip()
    # Direct parse
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Find first {...} block (handles markdown code fences)
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse LLM response as JSON: %r", content[:200])
    return None


def get_llm_score(
    offer_data: dict,
    criteria_data: dict,
    config: LMStudioConfig,
) -> dict | None:
    """Call LM Studio and return a parsed scoring dict.

    Returns ``None`` when LM Studio is disabled, unavailable, or returns an
    unparseable response.  The caller must handle the ``None`` case gracefully.
    """
    if not config.enabled:
        return None

    try:
        # Import here so the module can be imported without openai installed
        from openai import OpenAI  # noqa: PLC0415

        client = OpenAI(base_url=config.base_url, api_key=config.api_key)
        prompt = build_llm_prompt(offer_data, criteria_data)

        response = client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
        content = response.choices[0].message.content or ""
        return _parse_json_response(content)

    except Exception as exc:  # noqa: BLE001
        logger.warning("LM Studio call failed (%s: %s) – skipping LLM scoring.", type(exc).__name__, exc)
        return None
