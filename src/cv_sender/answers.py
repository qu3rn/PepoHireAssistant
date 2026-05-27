"""Automatic short-answer generation for job application form questions.

Architecture
------------
Answers are produced via a layered strategy:

1. **Rules** – deterministic answers for known fact-based fields
   (salary, availability, notice period, English level, work mode).
2. **Templates** – fill-in-the-blank answers for common motivational /
   experience questions drawn from :class:`~cv_sender.config.AnswerTemplatesConfig`.
3. **LM Studio** – if enabled and a rule/template does not apply, call the
   local LLM with a strict prompt.  On failure falls back to a generic safe
   answer.

Safety
------
* A simple hallucination guard checks whether the generated answer mentions
  technologies or claims not backed by profile / offer facts.
* Low-confidence answers are not auto-filled unless settings explicitly allow it.
* Generated answer text is stored only as a 120-character preview in logs.
"""

from __future__ import annotations

import json
import logging
import re
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from cv_sender.config import AnswerGenerationConfig, AnswerProfileConfig, AnswerTemplatesConfig, LMStudioConfig
    from cv_sender.cv_profiles import CVProfile
    from cv_sender.models import Offer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Question types
# ---------------------------------------------------------------------------


class QuestionType(StrEnum):
    WHY_COMPANY = "why_company"
    MOTIVATION = "motivation"
    EXPERIENCE = "experience"
    TECHNOLOGY_EXPERIENCE = "technology_experience"
    SALARY_EXPECTATION = "salary_expectation"
    AVAILABILITY = "availability"
    NOTICE_PERIOD = "notice_period"
    ENGLISH_LEVEL = "english_level"
    WORK_MODE = "work_mode"
    RELOCATION = "relocation"
    GENERAL_BIO = "general_bio"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class GeneratedAnswer(BaseModel):
    """One generated answer for one question."""

    question: str
    question_type: QuestionType = QuestionType.UNKNOWN
    answer: str = ""
    confidence: float = 0.0
    source: str = "rules"          # "template" | "rules" | "llm"
    warnings: list[str] = Field(default_factory=list)


class GeneratedAnswerSummary(BaseModel):
    """Compact record stored in FillResult / debug log — no full answer text."""

    question: str
    question_type: str = ""
    source: str = ""
    confidence: float = 0.0
    filled: bool = False
    warnings: list[str] = Field(default_factory=list)
    answer_preview: str = ""       # max 120 chars


# ---------------------------------------------------------------------------
# Question classification
# ---------------------------------------------------------------------------

# Each entry: (QuestionType, list-of-keyword-fragments, languages)
_CLASSIFICATION_RULES: list[tuple[QuestionType, list[str]]] = [
    (QuestionType.SALARY_EXPECTATION, [
        "salary", "wynagrodzenie", "oczekiwania finansowe", "zarobki", "stawka",
        "wage", "compensation", "pay expectation",
    ]),
    (QuestionType.AVAILABILITY, [
        "availability", "dostępność", "kiedy możesz", "start date", "od kiedy",
        "how soon", "when can you", "start", "availab",
    ]),
    (QuestionType.NOTICE_PERIOD, [
        "notice period", "okres wypowiedzenia", "notice", "wypowiedzenia",
    ]),
    (QuestionType.ENGLISH_LEVEL, [
        "english level", "poziom angielskiego", "język angielski",
        "english proficiency", "your english",
    ]),
    (QuestionType.WORK_MODE, [
        "work mode", "tryb pracy", "praca zdalna", "remote", "on-site", "hybrid",
        "model pracy", "remote work",
    ]),
    (QuestionType.RELOCATION, [
        "relocation", "przeprowadzka", "relokacja", "willing to relocate",
    ]),
    (QuestionType.WHY_COMPANY, [
        "dlaczego chcesz pracować", "why do you want", "why this company",
        "co cię przyciąga", "why join", "zainteresowanie firmą", "dlaczego nas",
        "why are you interested", "co motywuje cię do",
    ]),
    (QuestionType.MOTIVATION, [
        "motywacja", "motivation", "co cię motywuje", "what motivates",
        "why apply", "czemu aplikujesz", "co cię skłoniło",
    ]),
    (QuestionType.TECHNOLOGY_EXPERIENCE, [
        "doświadczenie z technologi", "experience with", "opisz swoje doświadczenie z",
        "describe your experience", "tell us about your experience", "your experience with",
        "techniki", "stack", "frameworks", "narzędzia",
    ]),
    (QuestionType.EXPERIENCE, [
        "opisz swoje doświadczenie", "years of experience", "lata doświadczenia",
        "background", "previous experience", "work experience", "doświadczenie zawodowe",
    ]),
    (QuestionType.GENERAL_BIO, [
        "tell us about yourself", "opowiedz o sobie", "kim jesteś",
        "introduce yourself", "krótkie podsumowanie", "short bio", "about you",
    ]),
]


def classify_application_question(question_text: str) -> QuestionType:
    """Return the best-matching :class:`QuestionType` for *question_text*.

    Matching is case-insensitive and checks all registered keyword fragments.
    Returns :attr:`QuestionType.UNKNOWN` when no rule fires.
    """
    lower = question_text.lower()
    for qtype, keywords in _CLASSIFICATION_RULES:
        if any(kw in lower for kw in keywords):
            return qtype
    return QuestionType.UNKNOWN


# ---------------------------------------------------------------------------
# Hallucination guard
# ---------------------------------------------------------------------------

_SUSPICIOUS_PHRASES = [
    r"\bexpert\b",
    r"\bled a team\b",
    r"\bmanaged a team\b",
    r"\bteam lead\b",
    r"\b10\+? years\b",
    r"\bmany years\b",
    r"\bdecades? of experience\b",
]


def _hallucination_warnings(
    answer: str,
    known_techs: set[str],
) -> list[str]:
    """Return a list of warning strings if the answer looks suspicious."""
    warnings: list[str] = []
    lower = answer.lower()

    # 1. Suspicious phrases
    for pattern in _SUSPICIOUS_PHRASES:
        if re.search(pattern, lower):
            warnings.append(f"Suspicious claim detected: '{pattern}'. Verify before submitting.")

    # 2. Technologies mentioned but not in profile/offer
    tech_pattern = re.compile(r"\b([A-Z][a-z]*(?:\.[a-z]+)?|[A-Z]{2,})\b")
    mentioned_words = {m.group() for m in tech_pattern.finditer(answer)}
    known_lower = {t.lower() for t in known_techs}
    for word in mentioned_words:
        if len(word) >= 3 and word.lower() not in known_lower:
            # only flag well-known tech-like proper nouns
            if any(word.lower().startswith(p) for p in (
                "react", "vue", "angular", "node", "python", "java", "typescript",
                "javascript", "sql", "aws", "azure", "docker", "kubernetes",
                "graphql", "redis", "postgres", "mongodb",
            )):
                warnings.append(
                    f"Technology '{word}' mentioned but not found in your profile or the offer."
                )

    return warnings


# ---------------------------------------------------------------------------
# Answer builders (rules / templates)
# ---------------------------------------------------------------------------

_ANSWER_UNAVAILABLE = ""


def _build_known_techs(
    offer: "Offer",
    cv_profile: "CVProfile | None",
    settings_techs: list[str],
) -> set[str]:
    """Collect all known technology names from all sources."""
    techs: set[str] = set()
    techs.update(offer.technologies)
    techs.update(settings_techs)
    if cv_profile:
        techs.update(cv_profile.technologies)
    return techs


def _rule_answer(
    qtype: QuestionType,
    answer_profile: "AnswerProfileConfig",
    offer: "Offer",
    cv_profile: "CVProfile | None",
    templates: "AnswerTemplatesConfig",
    max_chars: int,
) -> GeneratedAnswer | None:
    """Return a deterministic :class:`GeneratedAnswer` for *qtype*, or ``None``."""

    # Salary
    if qtype == QuestionType.SALARY_EXPECTATION:
        salary = ""
        if answer_profile.salary_b2b or answer_profile.salary_uop:
            parts = []
            if answer_profile.salary_b2b:
                parts.append(f"B2B: {answer_profile.salary_b2b}")
            if answer_profile.salary_uop:
                parts.append(f"UoP: {answer_profile.salary_uop}")
            salary = " / ".join(parts)
        if not salary:
            return None
        template = templates.salary or "{salary_expectation}"
        answer = template.format(salary_expectation=salary)[:max_chars]
        return GeneratedAnswer(
            question="", question_type=qtype, answer=answer,
            confidence=1.0, source="rules",
        )

    # Availability
    if qtype == QuestionType.AVAILABILITY:
        avail = answer_profile.english_level  # re-use availability from profile
        # The AnswerProfileConfig does not have its own availability field;
        # availability comes from Profile – callers pass it via answer_profile
        # using the short_bio fallback or we read it directly.
        # We fall through to let callers inject via a custom field below.
        return None

    # Notice period – handled the same way as availability via caller injection
    if qtype == QuestionType.NOTICE_PERIOD:
        return None

    # English level
    if qtype == QuestionType.ENGLISH_LEVEL:
        level = answer_profile.english_level
        if not level:
            return None
        answer = f"My English level is {level}."[:max_chars]
        return GeneratedAnswer(
            question="", question_type=qtype, answer=answer,
            confidence=1.0, source="rules",
        )

    # Work mode
    if qtype == QuestionType.WORK_MODE:
        return None  # no generic deterministic answer possible without knowing offer mode

    return None


def _template_answer(
    qtype: QuestionType,
    answer_profile: "AnswerProfileConfig",
    offer: "Offer",
    cv_profile: "CVProfile | None",
    templates: "AnswerTemplatesConfig",
    known_techs: set[str],
    max_chars: int,
) -> GeneratedAnswer | None:
    """Fill a template-based answer if sufficient context exists."""

    if qtype in (QuestionType.WHY_COMPANY, QuestionType.MOTIVATION):
        # Need some facts
        if not (answer_profile.motivation_general or templates.why_company):
            return None
        role = offer.title or "this role"
        offer_techs = ", ".join(offer.technologies[:3]) if offer.technologies else "the required stack"
        base = answer_profile.motivation_general or templates.why_company
        answer = base.format(
            technologies=offer_techs,
            role=role,
            company=offer.company or "your company",
        )[:max_chars]
        warnings = _hallucination_warnings(answer, known_techs)
        return GeneratedAnswer(
            question="", question_type=qtype, answer=answer,
            confidence=0.75, source="template", warnings=warnings,
        )

    if qtype == QuestionType.TECHNOLOGY_EXPERIENCE:
        skills = cv_profile.technologies if cv_profile else []
        if not skills:
            skills = list(known_techs)[:5]
        if not skills:
            return None
        tech_list = ", ".join(skills[:5])
        answer = f"I have hands-on experience with {tech_list}. {templates.react_experience}"[:max_chars]
        warnings = _hallucination_warnings(answer, known_techs)
        return GeneratedAnswer(
            question="", question_type=qtype, answer=answer,
            confidence=0.70, source="template", warnings=warnings,
        )

    if qtype in (QuestionType.EXPERIENCE, QuestionType.GENERAL_BIO):
        bio = answer_profile.short_bio
        if not bio:
            return None
        yrs = f" ({answer_profile.years_experience})" if answer_profile.years_experience else ""
        answer = f"{bio}{yrs}"[:max_chars]
        warnings = _hallucination_warnings(answer, known_techs)
        return GeneratedAnswer(
            question="", question_type=qtype, answer=answer,
            confidence=0.72, source="template", warnings=warnings,
        )

    return None


# ---------------------------------------------------------------------------
# LLM answer generator
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = (
    "You are a professional job application assistant. "
    "You generate short, honest answers for application forms. "
    "Return ONLY valid JSON – no markdown, no extra text. "
    "Never invent experience. Never exaggerate claims. "
    "Use only the provided candidate facts."
)

_LLM_USER_PROMPT = """\
Generate a short answer for the following application question.

Question: {question}

Candidate facts:
{facts_json}

Offer context:
- Title: {offer_title}
- Company: {offer_company}
- Technologies: {offer_techs}

Rules:
- Answer in the SAME language as the question (Polish if question is Polish, English otherwise).
- Maximum {max_chars} characters.
- 2-4 sentences maximum.
- Do NOT invent experience.
- Do NOT make claims not supported by the provided facts.
- Do NOT mention technologies not in the facts or offer.

Return JSON:
{{
  "answer": "<answer text>",
  "confidence": <float 0.0-1.0>,
  "warnings": ["<optional warning>"]
}}
"""


def _llm_answer(
    question: str,
    qtype: QuestionType,
    answer_profile: "AnswerProfileConfig",
    offer: "Offer",
    cv_profile: "CVProfile | None",
    known_techs: set[str],
    llm_config: "LMStudioConfig",
    max_chars: int,
) -> GeneratedAnswer:
    """Call LM Studio; return safe fallback on any failure."""

    facts: dict = {
        "short_bio": answer_profile.short_bio,
        "years_experience": answer_profile.years_experience,
        "strongest_skills": answer_profile.strongest_skills,
        "industries": answer_profile.industries,
        "work_style": answer_profile.work_style,
        "motivation": answer_profile.motivation_general,
        "salary_b2b": answer_profile.salary_b2b,
        "salary_uop": answer_profile.salary_uop,
        "english_level": answer_profile.english_level,
        "cv_technologies": cv_profile.technologies if cv_profile else [],
    }
    if not any(facts.values()):
        return GeneratedAnswer(
            question=question, question_type=qtype,
            answer="",
            confidence=0.0, source="llm",
            warnings=["answer_profile is empty; cannot generate answer via LLM."],
        )

    prompt = _LLM_USER_PROMPT.format(
        question=question,
        facts_json=json.dumps(facts, ensure_ascii=False, indent=2),
        offer_title=offer.title,
        offer_company=offer.company,
        offer_techs=", ".join(offer.technologies),
        max_chars=max_chars,
    )

    try:
        from openai import OpenAI  # noqa: PLC0415

        client = OpenAI(base_url=llm_config.base_url, api_key=llm_config.api_key)
        response = client.chat.completions.create(
            model=llm_config.model,
            messages=[
                {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        raw = (response.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("LM Studio answer generation failed (%s: %s).", type(exc).__name__, exc)
        return GeneratedAnswer(
            question=question, question_type=qtype,
            answer="",
            confidence=0.0, source="llm",
            warnings=[f"LM Studio unavailable: {exc}"],
        )

    # Parse JSON response
    parsed: dict | None = None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError:
                pass

    if not parsed:
        logger.warning("LLM answer response not parseable: %r", raw[:200])
        return GeneratedAnswer(
            question=question, question_type=qtype,
            answer="",
            confidence=0.0, source="llm",
            warnings=["LLM returned unparseable response."],
        )

    answer_text = str(parsed.get("answer", ""))[:max_chars]
    confidence = float(parsed.get("confidence", 0.5))
    llm_warnings = [str(w) for w in parsed.get("warnings", [])]

    # Run hallucination guard
    guard_warnings = _hallucination_warnings(answer_text, known_techs)
    all_warnings = llm_warnings + guard_warnings

    return GeneratedAnswer(
        question=question, question_type=qtype,
        answer=answer_text,
        confidence=confidence, source="llm",
        warnings=all_warnings,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_answer_for_question(
    question_text: str,
    offer: "Offer",
    answer_profile: "AnswerProfileConfig",
    cv_profile: "CVProfile | None",
    templates: "AnswerTemplatesConfig",
    answer_cfg: "AnswerGenerationConfig",
    llm_config: "LMStudioConfig | None",
    settings_techs: list[str] | None = None,
    *,
    availability_override: str = "",
    notice_period_override: str = "",
) -> GeneratedAnswer:
    """Generate a safe, short answer for one *question_text*.

    Strategy: rules → templates → LLM fallback.
    """
    qtype = classify_application_question(question_text)
    max_chars = answer_cfg.max_answer_chars
    known_techs = _build_known_techs(offer, cv_profile, settings_techs or [])

    # Special rules for fields where we always have deterministic data
    if qtype == QuestionType.AVAILABILITY and availability_override:
        answer = availability_override[:max_chars]
        result = GeneratedAnswer(
            question=question_text, question_type=qtype, answer=answer,
            confidence=1.0, source="rules",
        )
        return result

    if qtype == QuestionType.NOTICE_PERIOD and notice_period_override:
        answer = notice_period_override[:max_chars]
        return GeneratedAnswer(
            question=question_text, question_type=qtype, answer=answer,
            confidence=1.0, source="rules",
        )

    # 1. Rules
    rule_result = _rule_answer(qtype, answer_profile, offer, cv_profile, templates, max_chars)
    if rule_result is not None:
        rule_result.question = question_text
        return rule_result

    # 2. Templates
    tmpl_result = _template_answer(qtype, answer_profile, offer, cv_profile, templates, known_techs, max_chars)
    if tmpl_result is not None:
        tmpl_result.question = question_text
        return tmpl_result

    # 3. LLM
    if answer_cfg.use_llm and llm_config and llm_config.enabled:
        return _llm_answer(
            question_text, qtype, answer_profile, offer, cv_profile,
            known_techs, llm_config, max_chars,
        )

    # No answer available
    return GeneratedAnswer(
        question=question_text, question_type=qtype,
        answer="",
        confidence=0.0, source="rules",
        warnings=["No answer could be generated for this question type."],
    )


def generate_answers_for_form_questions(
    questions: list[str],
    offer: "Offer",
    answer_profile: "AnswerProfileConfig",
    cv_profile: "CVProfile | None",
    templates: "AnswerTemplatesConfig",
    answer_cfg: "AnswerGenerationConfig",
    llm_config: "LMStudioConfig | None",
    settings_techs: list[str] | None = None,
    *,
    availability_override: str = "",
    notice_period_override: str = "",
) -> list[GeneratedAnswer]:
    """Generate answers for each question in *questions*."""
    return [
        generate_answer_for_question(
            q, offer, answer_profile, cv_profile, templates, answer_cfg, llm_config,
            settings_techs=settings_techs,
            availability_override=availability_override,
            notice_period_override=notice_period_override,
        )
        for q in questions
    ]


def to_summary(answer: GeneratedAnswer, *, filled: bool = False) -> GeneratedAnswerSummary:
    """Convert a :class:`GeneratedAnswer` to a compact :class:`GeneratedAnswerSummary`."""
    return GeneratedAnswerSummary(
        question=answer.question,
        question_type=str(answer.question_type),
        source=answer.source,
        confidence=answer.confidence,
        filled=filled,
        warnings=list(answer.warnings),
        answer_preview=answer.answer[:120] if answer.answer else "",
    )
