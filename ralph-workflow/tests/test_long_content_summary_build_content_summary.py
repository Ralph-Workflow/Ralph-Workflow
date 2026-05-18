"""Tests for long_content_summary — headline extraction for oversized content."""

from __future__ import annotations

from ralph.display.long_content_summary import (
    build_content_summary,
)

_LONG = "x" * 5000
_SHORT = "x" * 100

_MAX_HEADLINE_LEN = 51  # max_chars=50 plus one ellipsis character
_MAX_CONTENT_SUMMARY_LEN = 201  # max_chars=200 plus one ellipsis character
_EXPECTED_2TUPLE = 2
_EXPECTED_4TUPLE = 4


class TestBuildContentSummary:
    def test_build_content_summary_picks_first_sentence(self) -> None:
        assert build_content_summary("First sentence. Second sentence.") == "First sentence."

    def test_build_content_summary_truncates_long_sentence(self) -> None:
        result = build_content_summary("a" * 300)
        assert result.endswith("…")
        assert len(result) <= _MAX_CONTENT_SUMMARY_LEN

    def test_build_content_summary_strips_markdown_prefix(self) -> None:
        assert build_content_summary("# Title\nbody") == "Title"

    def test_build_content_summary_empty_input(self) -> None:
        assert build_content_summary("") == ""

    def test_build_content_summary_exclamation_terminates(self) -> None:
        assert build_content_summary("Hello! World.") == "Hello!"

    def test_build_content_summary_question_terminates(self) -> None:
        assert build_content_summary("Done? Yes.") == "Done?"

    def test_build_content_summary_fallback_to_first_line(self) -> None:
        result = build_content_summary("no terminator here")
        assert result == "no terminator here"

    def test_build_content_summary_all_empty_returns_empty(self) -> None:
        assert build_content_summary("\n\n\n") == ""
