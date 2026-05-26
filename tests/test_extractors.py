"""Tests for source-specific and generic offer extractors.

All HTML is provided as inline fixture strings – no network calls are made.
The ``conftest.py`` ``disable_http_fetch`` fixture ensures ``_fetch_html`` is
always mocked to return ``None``, so these tests never touch the internet.
"""

from __future__ import annotations

import json

import pytest

from cv_sender.extractors.base import (
    JSON_LD,
    EMBEDDED_STATE,
    DOM,
    URL_ONLY,
    normalize_salary,
    normalize_contract,
    normalize_currency,
    normalize_technologies,
    clean_description,
    parse_json_ld_jobposting,
    parse_next_data,
    draft_from_json_ld,
)
from cv_sender.extractors.generic import GenericExtractor
from cv_sender.extractors.rocketjobs import RocketJobsExtractor
from cv_sender.extractors.justjoin import JustJoinExtractor
from cv_sender.extractors.nofluffjobs import NoFluffJobsExtractor
from cv_sender.extractors.pracuj import PracujExtractor
from cv_sender.extractors import get_extractor


# ---------------------------------------------------------------------------
# Fixture HTML builders
# ---------------------------------------------------------------------------


def _html_with_json_ld(data: dict) -> str:
    """Wrap a dict in a minimal HTML page with a JSON-LD script tag."""
    return (
        f'<html><head>'
        f'<title>Test Page</title>'
        f'<script type="application/ld+json">{json.dumps(data)}</script>'
        f'</head><body></body></html>'
    )


def _html_with_next_data(data: dict) -> str:
    """Wrap a dict in a minimal HTML page with a __NEXT_DATA__ script tag."""
    return (
        f'<html><head><title>Test Page</title></head><body>'
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
        f'</body></html>'
    )


# ---------------------------------------------------------------------------
# Helper normalizers
# ---------------------------------------------------------------------------


class TestNormalizeSalary:
    def test_integer(self) -> None:
        assert normalize_salary(15000) == 15000.0

    def test_float(self) -> None:
        assert normalize_salary(12500.5) == 12500.5

    def test_string_plain(self) -> None:
        assert normalize_salary("15000") == 15000.0

    def test_string_with_spaces(self) -> None:
        assert normalize_salary("15 000") == 15000.0

    def test_string_with_currency(self) -> None:
        assert normalize_salary("15000 PLN") == 15000.0

    def test_none_returns_none(self) -> None:
        assert normalize_salary(None) is None

    def test_zero_returns_none(self) -> None:
        assert normalize_salary(0) is None

    def test_empty_string_returns_none(self) -> None:
        assert normalize_salary("") is None

    def test_non_numeric_returns_none(self) -> None:
        assert normalize_salary("negotiable") is None


class TestNormalizeContract:
    def test_b2b_variants(self) -> None:
        for val in ["B2B", "b2b", "Business-to-business"]:
            assert normalize_contract(val) == "B2B", f"failed for {val!r}"

    def test_uop_variants(self) -> None:
        for val in ["UoP", "umowa o pracę", "permanent", "full_time"]:
            assert normalize_contract(val) == "UoP", f"failed for {val!r}"

    def test_internship(self) -> None:
        assert normalize_contract("Internship") == "Internship"
        assert normalize_contract("staż") == "Internship"

    def test_any(self) -> None:
        assert normalize_contract("B2B + UoP") == "Any"

    def test_empty_returns_empty(self) -> None:
        assert normalize_contract("") == ""

    def test_none_returns_empty(self) -> None:
        assert normalize_contract(None) == ""


class TestNormalizeCurrency:
    def test_pln(self) -> None:
        assert normalize_currency("PLN") == "PLN"

    def test_eur_lowercase(self) -> None:
        assert normalize_currency("eur") == "EUR"

    def test_unknown_defaults_to_pln(self) -> None:
        assert normalize_currency("CREDITS") == "PLN"

    def test_none_defaults_to_pln(self) -> None:
        assert normalize_currency(None) == "PLN"


class TestNormalizeTechnologies:
    def test_list_of_strings(self) -> None:
        result = normalize_technologies(["React", "TypeScript", "Node.js"])
        assert result == ["React", "TypeScript", "Node.js"]

    def test_list_of_dicts_with_name(self) -> None:
        result = normalize_technologies([{"name": "React"}, {"name": "TypeScript"}])
        assert result == ["React", "TypeScript"]

    def test_deduplicates_case_insensitive(self) -> None:
        result = normalize_technologies(["React", "react", "REACT"])
        assert result == ["React"]

    def test_comma_separated_string(self) -> None:
        result = normalize_technologies("React, TypeScript, Node.js")
        assert result == ["React", "TypeScript", "Node.js"]

    def test_empty_list(self) -> None:
        assert normalize_technologies([]) == []

    def test_none_returns_empty(self) -> None:
        assert normalize_technologies(None) == []


class TestCleanDescription:
    def test_strips_html_tags(self) -> None:
        result = clean_description("<p>Hello <strong>world</strong></p>")
        assert "<" not in result
        assert "Hello" in result
        assert "world" in result

    def test_collapses_whitespace(self) -> None:
        result = clean_description("hello   \n\n   world")
        assert result == "hello world"

    def test_empty_string(self) -> None:
        assert clean_description("") == ""

    def test_plain_text_unchanged(self) -> None:
        assert clean_description("plain text") == "plain text"


# ---------------------------------------------------------------------------
# JSON-LD parsing
# ---------------------------------------------------------------------------


class TestParseJsonLdJobPosting:
    def test_finds_jobposting_object(self) -> None:
        data = {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Frontend Dev",
        }
        html = _html_with_json_ld(data)
        result = parse_json_ld_jobposting(html)
        assert result is not None
        assert result["title"] == "Frontend Dev"

    def test_finds_jobposting_in_array(self) -> None:
        data = [
            {"@type": "Organization", "name": "ACME"},
            {"@type": "JobPosting", "title": "Backend Dev"},
        ]
        html = _html_with_json_ld(data)
        result = parse_json_ld_jobposting(html)
        assert result is not None
        assert result["title"] == "Backend Dev"

    def test_returns_none_when_no_jobposting(self) -> None:
        data = {"@type": "Organization", "name": "ACME"}
        html = _html_with_json_ld(data)
        assert parse_json_ld_jobposting(html) is None

    def test_returns_none_for_empty_html(self) -> None:
        assert parse_json_ld_jobposting("<html></html>") is None


class TestParseNextData:
    def test_extracts_json(self) -> None:
        data = {"props": {"pageProps": {"offer": {"title": "Test"}}}}
        html = _html_with_next_data(data)
        result = parse_next_data(html)
        assert result is not None
        assert result["props"]["pageProps"]["offer"]["title"] == "Test"

    def test_returns_none_when_missing(self) -> None:
        assert parse_next_data("<html></html>") is None


# ---------------------------------------------------------------------------
# draft_from_json_ld – full field extraction
# ---------------------------------------------------------------------------


class TestDraftFromJsonLd:
    _FULL_LD = {
        "@type": "JobPosting",
        "title": "Senior React Developer",
        "hiringOrganization": {"@type": "Organization", "name": "TechCorp"},
        "baseSalary": {
            "currency": "PLN",
            "value": {"@type": "QuantitativeValue", "minValue": 15000, "maxValue": 20000},
        },
        "jobLocation": {"address": {"addressLocality": "Warsaw"}},
        "description": "<p>Join our team.</p>",
        "employmentType": "CONTRACTOR",
        "skills": "React, TypeScript, GraphQL",
    }

    def test_title(self) -> None:
        draft = draft_from_json_ld(self._FULL_LD)
        assert draft.title == "Senior React Developer"

    def test_company(self) -> None:
        draft = draft_from_json_ld(self._FULL_LD)
        assert draft.company == "TechCorp"

    def test_salary(self) -> None:
        draft = draft_from_json_ld(self._FULL_LD)
        assert draft.salary_min == 15000.0
        assert draft.salary_max == 20000.0
        assert draft.currency == "PLN"

    def test_location(self) -> None:
        draft = draft_from_json_ld(self._FULL_LD)
        assert draft.location == "Warsaw"

    def test_contract(self) -> None:
        draft = draft_from_json_ld(self._FULL_LD)
        assert draft.contract == "B2B"

    def test_technologies(self) -> None:
        draft = draft_from_json_ld(self._FULL_LD)
        assert "React" in draft.technologies
        assert "TypeScript" in draft.technologies

    def test_description_stripped(self) -> None:
        draft = draft_from_json_ld(self._FULL_LD)
        assert "<" not in draft.description
        assert "Join our team" in draft.description

    def test_extraction_source_is_json_ld(self) -> None:
        draft = draft_from_json_ld(self._FULL_LD)
        assert draft.extraction_source == JSON_LD

    def test_high_confidence(self) -> None:
        draft = draft_from_json_ld(self._FULL_LD)
        assert draft.extraction_confidence >= 0.6


# ---------------------------------------------------------------------------
# GenericExtractor
# ---------------------------------------------------------------------------


class TestGenericExtractor:
    _extractor = GenericExtractor()

    def test_can_handle_any_url(self) -> None:
        assert self._extractor.can_handle("https://anything.com/job/1")

    def test_extracts_json_ld(self) -> None:
        ld = {
            "@type": "JobPosting",
            "title": "Full Stack Dev",
            "hiringOrganization": {"name": "WidgetCo"},
        }
        html = _html_with_json_ld(ld)
        draft = self._extractor.extract("https://example.com/job/1", html)
        assert draft.title == "Full Stack Dev"
        assert draft.company == "WidgetCo"
        assert draft.extraction_source == JSON_LD

    def test_falls_back_to_title_tag(self) -> None:
        html = "<html><head><title>Backend Developer – ACME</title></head><body></body></html>"
        draft = self._extractor.extract("https://example.com/job/1", html)
        assert "Backend Developer" in draft.title
        assert draft.extraction_source == DOM

    def test_empty_html_returns_empty_draft(self) -> None:
        draft = self._extractor.extract("https://example.com", "")
        assert draft.title == ""
        assert draft.extraction_confidence == 0.0


# ---------------------------------------------------------------------------
# RocketJobsExtractor
# ---------------------------------------------------------------------------


class TestRocketJobsExtractor:
    _extractor = RocketJobsExtractor()
    _url = "https://rocketjobs.pl/oferty/senior-frontend-123"

    _NEXT_DATA = {
        "props": {
            "pageProps": {
                "jobOffer": {
                    "title": "Senior Frontend Developer",
                    "employer": {"name": "Startup Inc"},
                    "city": "Warsaw",
                    "minimalSalary": 15000,
                    "maximalSalary": 20000,
                    "currency": "PLN",
                    "employmentType": {"name": "B2B"},
                    "requiredSkills": [{"name": "React"}, {"name": "TypeScript"}],
                    "niceToHave": [{"name": "Next.js"}],
                    "description": "<p>We are looking for a senior developer.</p>",
                }
            }
        }
    }

    def test_can_handle_rocketjobs_url(self) -> None:
        assert self._extractor.can_handle(self._url)

    def test_does_not_handle_other_url(self) -> None:
        assert not self._extractor.can_handle("https://pracuj.pl/praca/dev")

    def test_extracts_title(self) -> None:
        html = _html_with_next_data(self._NEXT_DATA)
        draft = self._extractor.extract(self._url, html)
        assert draft.title == "Senior Frontend Developer"

    def test_extracts_company(self) -> None:
        html = _html_with_next_data(self._NEXT_DATA)
        draft = self._extractor.extract(self._url, html)
        assert draft.company == "Startup Inc"

    def test_extracts_salary(self) -> None:
        html = _html_with_next_data(self._NEXT_DATA)
        draft = self._extractor.extract(self._url, html)
        assert draft.salary_min == 15000.0
        assert draft.salary_max == 20000.0
        assert draft.currency == "PLN"

    def test_extracts_location(self) -> None:
        html = _html_with_next_data(self._NEXT_DATA)
        draft = self._extractor.extract(self._url, html)
        assert draft.location == "Warsaw"

    def test_extracts_contract_b2b(self) -> None:
        html = _html_with_next_data(self._NEXT_DATA)
        draft = self._extractor.extract(self._url, html)
        assert draft.contract == "B2B"

    def test_extracts_technologies(self) -> None:
        html = _html_with_next_data(self._NEXT_DATA)
        draft = self._extractor.extract(self._url, html)
        assert "React" in draft.technologies
        assert "TypeScript" in draft.technologies
        assert "Next.js" in draft.technologies

    def test_extraction_source_is_embedded_state(self) -> None:
        html = _html_with_next_data(self._NEXT_DATA)
        draft = self._extractor.extract(self._url, html)
        assert draft.extraction_source == EMBEDDED_STATE

    def test_returns_empty_draft_for_missing_data(self) -> None:
        html = _html_with_next_data({"props": {"pageProps": {}}})
        draft = self._extractor.extract(self._url, html)
        assert draft.title == ""

    def test_prefers_json_ld_over_next_data(self) -> None:
        ld = {
            "@type": "JobPosting",
            "title": "From JSON-LD",
            "hiringOrganization": {"name": "Org"},
        }
        # Provide both JSON-LD and __NEXT_DATA__
        html = (
            _html_with_json_ld(ld)[:-14]  # strip </html>
            + f'<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(self._NEXT_DATA)
            + "</script></html>"
        )
        draft = self._extractor.extract(self._url, html)
        assert draft.title == "From JSON-LD"


# ---------------------------------------------------------------------------
# JustJoinExtractor
# ---------------------------------------------------------------------------


class TestJustJoinExtractor:
    _extractor = JustJoinExtractor()
    _url = "https://justjoin.it/offers/backend-developer-456"

    _NEXT_DATA = {
        "props": {
            "pageProps": {
                "offer": {
                    "title": "Backend Developer",
                    "companyName": "TechStartup",
                    "city": "Kraków",
                    "salary": [
                        {"from": 12000, "to": 18000, "currency": "pln", "type": "b2b"},
                        {"from": 10000, "to": 14000, "currency": "pln", "type": "permanent"},
                    ],
                    "skills": [{"name": "Python"}, {"name": "Django"}],
                    "body": "Join our team of developers.",
                    "workplaceType": "remote",
                }
            }
        }
    }

    def test_can_handle_justjoin_url(self) -> None:
        assert self._extractor.can_handle(self._url)

    def test_does_not_handle_other_url(self) -> None:
        assert not self._extractor.can_handle("https://rocketjobs.pl/oferty/dev")

    def test_extracts_title(self) -> None:
        html = _html_with_next_data(self._NEXT_DATA)
        draft = self._extractor.extract(self._url, html)
        assert draft.title == "Backend Developer"

    def test_extracts_company(self) -> None:
        html = _html_with_next_data(self._NEXT_DATA)
        draft = self._extractor.extract(self._url, html)
        assert draft.company == "TechStartup"

    def test_prefers_b2b_salary(self) -> None:
        html = _html_with_next_data(self._NEXT_DATA)
        draft = self._extractor.extract(self._url, html)
        # Should pick the b2b salary entry
        assert draft.salary_min == 12000.0
        assert draft.salary_max == 18000.0
        assert draft.contract == "B2B"

    def test_extracts_skills(self) -> None:
        html = _html_with_next_data(self._NEXT_DATA)
        draft = self._extractor.extract(self._url, html)
        assert "Python" in draft.technologies
        assert "Django" in draft.technologies

    def test_appends_remote_to_location(self) -> None:
        html = _html_with_next_data(self._NEXT_DATA)
        draft = self._extractor.extract(self._url, html)
        assert "remote" in draft.location.lower()

    def test_extraction_source_is_embedded_state(self) -> None:
        html = _html_with_next_data(self._NEXT_DATA)
        draft = self._extractor.extract(self._url, html)
        assert draft.extraction_source == EMBEDDED_STATE


# ---------------------------------------------------------------------------
# NoFluffJobsExtractor
# ---------------------------------------------------------------------------


class TestNoFluffJobsExtractor:
    _extractor = NoFluffJobsExtractor()
    _url = "https://nofluffjobs.com/pl/job/full-stack-dev-acme-wroclaw"

    _JSON_LD = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Full Stack Developer",
        "hiringOrganization": {"@type": "Organization", "name": "NoFluff Inc"},
        "baseSalary": {
            "currency": "PLN",
            "value": {"@type": "QuantitativeValue", "minValue": 8000, "maxValue": 12000},
        },
        "jobLocation": {"address": {"addressLocality": "Wrocław"}},
        "description": "Full stack position in Wrocław.",
        "employmentType": "CONTRACTOR",
        "skills": "JavaScript, Node.js, React",
    }

    def test_can_handle_nofluffjobs_url(self) -> None:
        assert self._extractor.can_handle(self._url)

    def test_does_not_handle_other_url(self) -> None:
        assert not self._extractor.can_handle("https://justjoin.it/offers/dev")

    def test_extracts_from_json_ld(self) -> None:
        html = _html_with_json_ld(self._JSON_LD)
        draft = self._extractor.extract(self._url, html)
        assert draft.title == "Full Stack Developer"
        assert draft.company == "NoFluff Inc"
        assert draft.salary_min == 8000.0
        assert draft.salary_max == 12000.0
        assert draft.location == "Wrocław"
        assert draft.extraction_source == JSON_LD

    def test_technologies_from_json_ld(self) -> None:
        html = _html_with_json_ld(self._JSON_LD)
        draft = self._extractor.extract(self._url, html)
        assert "JavaScript" in draft.technologies
        assert "Node.js" in draft.technologies
        assert "React" in draft.technologies

    def test_next_data_fallback(self) -> None:
        next_data = {
            "props": {
                "pageProps": {
                    "post": {
                        "basics": {"title": "DevOps Engineer"},
                        "company": {"name": "CloudCo"},
                        "specs": {
                            "requirements": {
                                "technologies": ["Docker", "Kubernetes"]
                            }
                        },
                    }
                }
            }
        }
        html = _html_with_next_data(next_data)
        draft = self._extractor.extract(self._url, html)
        assert draft.title == "DevOps Engineer"
        assert draft.company == "CloudCo"
        assert "Docker" in draft.technologies


# ---------------------------------------------------------------------------
# PracujExtractor
# ---------------------------------------------------------------------------


class TestPracujExtractor:
    _extractor = PracujExtractor()
    _url = "https://www.pracuj.pl/praca/java-developer,oferta,1234567"

    _JSON_LD = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Java Developer",
        "hiringOrganization": {"@type": "Organization", "name": "Big Corp"},
        "baseSalary": {
            "currency": "PLN",
            "value": {"@type": "QuantitativeValue", "minValue": 10000, "maxValue": 14000},
        },
        "jobLocation": {"address": {"addressLocality": "Warszawa"}},
        "description": "Looking for an experienced Java developer.",
        "employmentType": "FULL_TIME",
    }

    def test_can_handle_pracuj_url(self) -> None:
        assert self._extractor.can_handle(self._url)

    def test_does_not_handle_other_url(self) -> None:
        assert not self._extractor.can_handle("https://nofluffjobs.com/pl/job/dev")

    def test_extracts_from_json_ld(self) -> None:
        html = _html_with_json_ld(self._JSON_LD)
        draft = self._extractor.extract(self._url, html)
        assert draft.title == "Java Developer"
        assert draft.company == "Big Corp"
        assert draft.salary_min == 10000.0
        assert draft.salary_max == 14000.0
        assert draft.location == "Warszawa"

    def test_contract_full_time_maps_to_uop(self) -> None:
        html = _html_with_json_ld(self._JSON_LD)
        draft = self._extractor.extract(self._url, html)
        assert draft.contract == "UoP"

    def test_next_data_fallback(self) -> None:
        next_data = {
            "props": {
                "pageProps": {
                    "jobOffer": {
                        "title": "DevOps Lead",
                        "employer": {"name": "TechCo"},
                        "locations": [{"city": "Gdańsk"}],
                        "technologies": ["Terraform", "AWS"],
                    }
                }
            }
        }
        html = _html_with_next_data(next_data)
        draft = self._extractor.extract(self._url, html)
        assert draft.title == "DevOps Lead"
        assert draft.company == "TechCo"
        assert draft.location == "Gdańsk"
        assert "Terraform" in draft.technologies


# ---------------------------------------------------------------------------
# Extractor selection (get_extractor)
# ---------------------------------------------------------------------------


class TestGetExtractor:
    def test_rocketjobs_selected_for_rocketjobs_url(self) -> None:
        extractor = get_extractor("https://rocketjobs.pl/oferty/dev-123")
        assert isinstance(extractor, RocketJobsExtractor)

    def test_justjoin_selected_for_justjoin_url(self) -> None:
        extractor = get_extractor("https://justjoin.it/offers/frontend-456")
        assert isinstance(extractor, JustJoinExtractor)

    def test_nofluffjobs_selected(self) -> None:
        extractor = get_extractor("https://nofluffjobs.com/pl/job/dev-wroclaw")
        assert isinstance(extractor, NoFluffJobsExtractor)

    def test_pracuj_selected(self) -> None:
        extractor = get_extractor("https://www.pracuj.pl/praca/dev,oferta,123")
        assert isinstance(extractor, PracujExtractor)

    def test_generic_selected_for_unknown_url(self) -> None:
        extractor = get_extractor("https://linkedin.com/jobs/view/12345")
        assert isinstance(extractor, GenericExtractor)

    def test_generic_selected_for_random_url(self) -> None:
        extractor = get_extractor("https://example.com/careers/dev")
        assert isinstance(extractor, GenericExtractor)


# ---------------------------------------------------------------------------
# Fallback: source-specific extractor returns empty, generic kicks in
# ---------------------------------------------------------------------------


def test_generic_fallback_when_next_data_missing() -> None:
    """If __NEXT_DATA__ is absent, RocketJobs falls back gracefully."""
    extractor = RocketJobsExtractor()
    html = (
        "<html><head>"
        "<title>Senior Frontend Developer – RocketJobs</title>"
        "</head><body></body></html>"
    )
    # No __NEXT_DATA__ and no JSON-LD → extractor returns empty draft
    draft = extractor.extract("https://rocketjobs.pl/oferty/dev", html)
    assert draft.title == ""
    assert draft.extraction_confidence == 0.0


def test_salary_deduplication_in_technologies() -> None:
    """Technologies list must be deduplicated across required + niceToHave."""
    from cv_sender.extractors.rocketjobs import RocketJobsExtractor

    next_data = {
        "props": {
            "pageProps": {
                "jobOffer": {
                    "title": "Dev",
                    "employer": {"name": "Co"},
                    "city": "Warsaw",
                    "requiredSkills": [{"name": "React"}, {"name": "TypeScript"}],
                    "niceToHave": [{"name": "React"}, {"name": "GraphQL"}],  # React is duplicate
                }
            }
        }
    }
    html = _html_with_next_data(next_data)
    extractor = RocketJobsExtractor()
    draft = extractor.extract("https://rocketjobs.pl/oferty/dev", html)
    # React should appear only once
    assert draft.technologies.count("React") == 1
    assert "TypeScript" in draft.technologies
    assert "GraphQL" in draft.technologies
