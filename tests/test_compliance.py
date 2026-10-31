import pytest
from pydantic import ValidationError

from app.compliance import (
    RequestConstraintError, extract_constraints, normalize_section, section_word_count,
)
from app.schemas import Section


def test_extracts_agenda_constraints_without_a_model_call(agenda_case):
    constraints = extract_constraints(agenda_case[0])
    assert constraints.exact_section_count == 3
    assert constraints.required_headings == ["updates", "blockers", "next steps"]
    assert constraints.max_words_per_section == 39
    assert constraints.no_tables and constraints.compact


@pytest.mark.parametrize("phrase,limit", [("under 40",39), ("at most 40",40), ("no more than 40",40)])
def test_inclusive_and_exclusive_section_limits(phrase, limit):
    assert extract_constraints(f"Keep each section {phrase} words.").max_words_per_section == limit


def test_does_not_treat_total_document_limit_as_a_per_section_limit():
    assert extract_constraints("Write a report under 100 words.").max_words_per_section is None


def test_exact_section_counts_outside_capacity_fail_promptly():
    with pytest.raises(RequestConstraintError):
        extract_constraints("Use exactly 12 sections.")


def test_deduplication_handles_sentences_repeated_across_formats():
    section = Section(
        heading="Updates", paragraphs=["Share progress. Confirm priorities."],
        bullets=["Share progress.", "Confirm priorities. Explain any changes."], tables=[],
    )
    result = normalize_section(section)
    assert result.paragraphs == ["Share progress. Confirm priorities."]
    assert result.bullets == ["Explain any changes."]
    assert section_word_count(result) == 7


def test_bullet_only_sections_are_valid():
    section = Section(heading="Updates", paragraphs=[], bullets=["Share progress."], tables=[])
    assert section_word_count(section) == 2


@pytest.mark.parametrize("paragraphs,bullets", [([],[]), ([" "],[]), ([],["\t"])])
def test_empty_sections_are_rejected(paragraphs, bullets):
    with pytest.raises(ValidationError):
        Section(heading="Updates", paragraphs=paragraphs, bullets=bullets, tables=[])
