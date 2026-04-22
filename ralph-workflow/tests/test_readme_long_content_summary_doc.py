"""Regression tests: README 'Long-content display' section must stay in sync with code."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ralph.display.long_content_summary import _SUMMARY_THRESHOLD, should_summarize

_README_PATH = Path(__file__).resolve().parents[1] / "README.md"
_MIN_SECTION_LEN = 200


@pytest.fixture(scope="module")
def long_content_section() -> str:
    text = _README_PATH.read_text(encoding="utf-8")
    start = text.find("## Long-content display")
    assert start != -1, "README must contain a '## Long-content display' heading"
    end = text.find("\n## ", start + 1)
    return text[start:] if end == -1 else text[start:end]


def test_readme_lists_all_disabled_values(long_content_section: str) -> None:
    for value in ("0", "false", "no", "off"):
        assert value in long_content_section, (
            f"README 'Long-content display' must document disabled value '{value}'"
        )


def test_readme_states_default_on(long_content_section: str) -> None:
    match = re.search(r"default[^\n]*(on|enabled)", long_content_section, re.IGNORECASE)
    assert match is not None, (
        "README 'Long-content display' must state the summary is on/enabled by default"
    )


def test_readme_does_not_claim_opt_in_by_setting_flag_to_1(long_content_section: str) -> None:
    match = re.search(r"set[^\n]*RALPH_LONG_CONTENT_SUMMARY=1", long_content_section, re.IGNORECASE)
    assert match is None, (
        "README must not describe RALPH_LONG_CONTENT_SUMMARY=1 as the way to enable the summary"
    )


def test_readme_documents_200_char_inline_cap(long_content_section: str) -> None:
    assert "200" in long_content_section, (
        "README 'Long-content display' must document the 200-character inline summary cap"
    )
    assert "120" in long_content_section, (
        "README 'Long-content display' must document the 120-character streaming summary cap"
    )


def test_threshold_constant_matches_readme(long_content_section: str) -> None:
    assert str(_SUMMARY_THRESHOLD) in long_content_section, (
        f"README must document the summary threshold ({_SUMMARY_THRESHOLD})"
        " to match ralph.display.long_content_summary._SUMMARY_THRESHOLD"
    )


def test_section_is_non_empty(long_content_section: str) -> None:
    assert len(long_content_section) > _MIN_SECTION_LEN, (
        "README 'Long-content display' section must have content"
    )


def test_summary_threshold_is_positive() -> None:
    assert _SUMMARY_THRESHOLD > 0, "_SUMMARY_THRESHOLD must be a positive integer"


def test_should_summarize_above_threshold() -> None:
    assert should_summarize("x" * (_SUMMARY_THRESHOLD + 1), {}) is True, (
        "should_summarize must return True for text exceeding the threshold with no env flag"
    )


def test_readme_documents_deterministic_headline(long_content_section: str) -> None:
    assert "deterministic headline" in long_content_section, (
        "README 'Long-content display' must describe the deterministic headline layer"
    )


def test_readme_documents_ai_summary_label(long_content_section: str) -> None:
    assert "ai-summary" in long_content_section, (
        "README 'Long-content display' must document the '↳ ai-summary:' label"
    )


def test_readme_documents_no_headline_available(long_content_section: str) -> None:
    assert "(no headline available)" in long_content_section, (
        "README must document the '(no headline available)' placeholder text"
    )


def test_readme_documents_ralph_long_content_ai_summary(long_content_section: str) -> None:
    assert "RALPH_LONG_CONTENT_AI_SUMMARY" in long_content_section, (
        "README must document the RALPH_LONG_CONTENT_AI_SUMMARY opt-in env var"
    )
