"""Tests for cv_sender.answers – classification, rules, templates, LLM fallback."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cv_sender.answers import (
    GeneratedAnswer,
    GeneratedAnswerSummary,
    QuestionType,
    _hallucination_warnings,
    classify_application_question,
    generate_answer_for_question,
    generate_answers_for_form_questions,
    to_summary,
)
from cv_sender.config import (
    AnswerGenerationConfig,
    AnswerProfileConfig,
    AnswerTemplatesConfig,
    LMStudioConfig,
)
from cv_sender.models import FillResult, FillStatus, Offer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_offer(**kwargs) -> Offer:
    defaults = dict(
        id="offer-1",
        url="https://example.com/job/1",
        title="Frontend Developer",
        company="Acme Corp",
        technologies=["React", "TypeScript"],
        source="generic",
    )
    defaults.update(kwargs)
    return Offer(**defaults)


def _make_profile(**kwargs) -> AnswerProfileConfig:
    defaults = dict(
        short_bio="Frontend developer with React and TypeScript experience.",
        years_experience="5 years",
        strongest_skills=["React", "TypeScript"],
        motivation_general="I enjoy working on user-facing products.",
        salary_b2b="18000 PLN net + VAT",
        salary_uop="15000 PLN gross",
        english_level="C1",
    )
    defaults.update(kwargs)
    return AnswerProfileConfig(**defaults)


def _make_templates(**kwargs) -> AnswerTemplatesConfig:
    return AnswerTemplatesConfig(**kwargs)


def _make_cfg(**kwargs) -> AnswerGenerationConfig:
    defaults = dict(enabled=True, use_llm=False, auto_fill_generated_answers=True,
                    min_confidence_to_autofill=0.65, max_answer_chars=600)
    defaults.update(kwargs)
    return AnswerGenerationConfig(**defaults)


# ---------------------------------------------------------------------------
# Classification tests
# ---------------------------------------------------------------------------

class TestClassifyApplicationQuestion:
    def test_classify_polish_salary(self):
        assert classify_application_question("Jakie są Twoje oczekiwania finansowe?") == QuestionType.SALARY_EXPECTATION

    def test_classify_english_salary(self):
        assert classify_application_question("What is your salary expectation?") == QuestionType.SALARY_EXPECTATION

    def test_classify_english_availability(self):
        assert classify_application_question("When can you start?") == QuestionType.AVAILABILITY

    def test_classify_polish_availability(self):
        assert classify_application_question("Od kiedy możesz zacząć?") == QuestionType.AVAILABILITY

    def test_classify_why_company_polish(self):
        assert classify_application_question("Dlaczego chcesz pracować w tej firmie?") == QuestionType.WHY_COMPANY

    def test_classify_why_company_english(self):
        assert classify_application_question("Why do you want to join us?") == QuestionType.WHY_COMPANY

    def test_classify_english_level(self):
        assert classify_application_question("What is your English level?") == QuestionType.ENGLISH_LEVEL

    def test_classify_notice_period(self):
        assert classify_application_question("What is your notice period?") == QuestionType.NOTICE_PERIOD

    def test_classify_motivation(self):
        assert classify_application_question("Co cię motywuje?") == QuestionType.MOTIVATION

    def test_classify_unknown(self):
        assert classify_application_question("Describe your favorite food.") == QuestionType.UNKNOWN


# ---------------------------------------------------------------------------
# Rule-based / deterministic answers
# ---------------------------------------------------------------------------

class TestSalaryAnswerDeterministic:
    def test_salary_b2b_returns_confidence_1(self):
        result = generate_answer_for_question(
            "Jakie są Twoje oczekiwania finansowe?",
            _make_offer(),
            _make_profile(),
            None,
            _make_templates(),
            _make_cfg(),
            None,
        )
        assert result.confidence == 1.0
        assert result.source == "rules"
        assert "18000" in result.answer

    def test_salary_empty_profile_has_no_answer(self):
        profile = _make_profile(salary_b2b="", salary_uop="")
        result = generate_answer_for_question(
            "What is your salary expectation?",
            _make_offer(),
            profile,
            None,
            _make_templates(),
            _make_cfg(),
            None,
        )
        # No rule fires; also no LLM → fallback empty
        assert result.answer == "" or result.source in ("template", "llm")


class TestAvailabilityAnswerDeterministic:
    def test_availability_override(self):
        result = generate_answer_for_question(
            "When can you start?",
            _make_offer(),
            _make_profile(),
            None,
            _make_templates(),
            _make_cfg(),
            None,
            availability_override="Immediately",
        )
        assert result.answer == "Immediately"
        assert result.confidence == 1.0
        assert result.source == "rules"

    def test_notice_period_override(self):
        result = generate_answer_for_question(
            "What is your notice period?",
            _make_offer(),
            _make_profile(),
            None,
            _make_templates(),
            _make_cfg(),
            None,
            notice_period_override="2 weeks",
        )
        assert result.answer == "2 weeks"
        assert result.confidence == 1.0


class TestEnglishLevelAnswer:
    def test_english_level_returns_confidence_1(self):
        result = generate_answer_for_question(
            "What is your English level?",
            _make_offer(),
            _make_profile(english_level="B2"),
            None,
            _make_templates(),
            _make_cfg(),
            None,
        )
        assert result.confidence == 1.0
        assert "B2" in result.answer


# ---------------------------------------------------------------------------
# Hallucination guard
# ---------------------------------------------------------------------------

class TestHallucinationGuard:
    def test_no_warnings_for_known_tech(self):
        warnings = _hallucination_warnings("I have experience with React.", {"React", "TypeScript"})
        assert not warnings

    def test_warns_for_unknown_tech(self):
        warnings = _hallucination_warnings("I have experience with Angular.", {"React"})
        assert any("Angular" in w for w in warnings)

    def test_warns_for_suspicious_phrase_expert(self):
        warnings = _hallucination_warnings("I am an expert in React.", set())
        assert any("expert" in w.lower() or "Suspicious" in w for w in warnings)

    def test_warns_for_led_a_team(self):
        warnings = _hallucination_warnings("I led a team of 5 developers.", set())
        assert warnings  # at least one suspicious phrase warning


# ---------------------------------------------------------------------------
# Max answer length
# ---------------------------------------------------------------------------

class TestMaxAnswerLength:
    def test_answer_truncated_to_max_chars(self):
        cfg = _make_cfg(max_answer_chars=20)
        result = generate_answer_for_question(
            "What is your English level?",
            _make_offer(),
            _make_profile(english_level="C1"),
            None,
            _make_templates(),
            cfg,
            None,
        )
        assert len(result.answer) <= 20


# ---------------------------------------------------------------------------
# LLM fallback tests
# ---------------------------------------------------------------------------

class TestLlmFallback:
    def _llm_cfg(self) -> LMStudioConfig:
        return LMStudioConfig(enabled=True, base_url="http://localhost:1234/v1", api_key="x", model="test")

    def test_llm_exception_returns_empty_with_warning(self):
        cfg = _make_cfg(use_llm=True)
        llm_cfg = self._llm_cfg()
        with patch("openai.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.side_effect = ConnectionError("LM Studio down")
            result = generate_answer_for_question(
                "Describe yourself briefly.",
                _make_offer(),
                _make_profile(),
                None,
                _make_templates(),
                cfg,
                llm_cfg,
            )
        assert result.answer == ""
        assert result.warnings

    def test_llm_invalid_json_returns_empty_with_warning(self):
        cfg = _make_cfg(use_llm=True)
        llm_cfg = self._llm_cfg()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "not json at all {{{"
        with patch("openai.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = mock_response
            result = generate_answer_for_question(
                "Describe yourself briefly.",
                _make_offer(),
                _make_profile(),
                None,
                _make_templates(),
                cfg,
                llm_cfg,
            )
        assert result.answer == ""
        assert result.warnings

    def test_llm_valid_json_populates_answer(self):
        cfg = _make_cfg(use_llm=True)
        llm_cfg = self._llm_cfg()
        payload = '{"answer": "I have 5 years of React experience.", "confidence": 0.8, "warnings": []}'
        mock_response = MagicMock()
        mock_response.choices[0].message.content = payload
        with patch("openai.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = mock_response
            result = generate_answer_for_question(
                "Describe yourself briefly.",
                _make_offer(),
                _make_profile(),
                None,
                _make_templates(),
                cfg,
                llm_cfg,
            )
        assert "React" in result.answer
        assert result.confidence == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Batch generation
# ---------------------------------------------------------------------------

class TestBatchGeneration:
    def test_batch_returns_one_per_question(self):
        questions = [
            "Jakie są Twoje oczekiwania finansowe?",
            "When can you start?",
        ]
        results = generate_answers_for_form_questions(
            questions,
            _make_offer(),
            _make_profile(),
            None,
            _make_templates(),
            _make_cfg(),
            None,
            availability_override="2 weeks",
        )
        assert len(results) == 2
        assert all(isinstance(r, GeneratedAnswer) for r in results)


# ---------------------------------------------------------------------------
# to_summary
# ---------------------------------------------------------------------------

class TestToSummary:
    def test_to_summary_filled_true(self):
        ans = GeneratedAnswer(
            question="test?",
            question_type=QuestionType.SALARY_EXPECTATION,
            answer="18000 PLN",
            confidence=1.0,
            source="rules",
        )
        s = to_summary(ans, filled=True)
        assert isinstance(s, GeneratedAnswerSummary)
        assert s.filled is True
        assert s.answer_preview == "18000 PLN"

    def test_to_summary_low_confidence_not_filled(self):
        ans = GeneratedAnswer(
            question="test?",
            question_type=QuestionType.UNKNOWN,
            answer="maybe",
            confidence=0.3,
            source="template",
        )
        s = to_summary(ans, filled=False)
        assert s.filled is False
        assert s.confidence == pytest.approx(0.3)

    def test_answer_preview_truncated_to_120(self):
        long_answer = "x" * 300
        ans = GeneratedAnswer(
            question="q?",
            question_type=QuestionType.GENERAL_BIO,
            answer=long_answer,
            confidence=0.8,
            source="template",
        )
        s = to_summary(ans)
        assert len(s.answer_preview) <= 120


# ---------------------------------------------------------------------------
# FillResult.generated_answers
# ---------------------------------------------------------------------------

class TestFillResultGeneratedAnswers:
    def test_generated_answers_is_list(self):
        result = FillResult(status=FillStatus.FILLED, offer_id="x")
        assert isinstance(result.generated_answers, list)

    def test_generated_answers_appends_and_serializes(self):
        result = FillResult(status=FillStatus.FILLED, offer_id="x")
        ans = GeneratedAnswer(
            question="Why us?",
            question_type=QuestionType.WHY_COMPANY,
            answer="Because I love your product.",
            confidence=0.75,
            source="template",
        )
        summary = to_summary(ans, filled=True)
        result.generated_answers.append(summary.model_dump())
        assert len(result.generated_answers) == 1
        d = result.generated_answers[0]
        assert d["question"] == "Why us?"
        assert d["filled"] is True
