"""Tests for long_content_summary — headline extraction for oversized content."""

from __future__ import annotations

from ralph.display.long_content_summary import (
    build_headline_summary,
)

_LONG = "x" * 5000
_SHORT = "x" * 100

_MAX_HEADLINE_LEN = 51  # max_chars=50 plus one ellipsis character
_MAX_CONTENT_SUMMARY_LEN = 201  # max_chars=200 plus one ellipsis character
_EXPECTED_2TUPLE = 2
_EXPECTED_4TUPLE = 4


class TestBuildHeadlineSummary:
    def test_build_headline_summary_still_works(self) -> None:
        long_text = (
            "Long sentence that definitely runs longer than one hundred and twenty "
            "characters to test truncation at one-twenty cap please."
        )
        result = build_headline_summary(long_text)
        assert result.endswith("…")

    def test_first_nonempty_line_returned(self) -> None:
        text = "First line\nSecond line\nThird line"
        assert build_headline_summary(text) == "First line"

    def test_empty_first_lines_skipped(self) -> None:
        text = "\n\nActual content\nMore"
        assert build_headline_summary(text) == "Actual content"

    def test_markdown_heading_prefix_stripped(self) -> None:
        text = "# My Heading\nBody text"
        assert build_headline_summary(text) == "My Heading"

    def test_truncated_to_max_chars(self) -> None:
        text = "a" * 200
        result = build_headline_summary(text, max_chars=50)
        assert len(result) <= _MAX_HEADLINE_LEN
        assert result.endswith("…")

    def test_short_text_not_truncated(self) -> None:
        text = "Short text"
        result = build_headline_summary(text, max_chars=120)
        assert result == "Short text"
        assert "…" not in result

    def test_empty_text_returns_empty_string(self) -> None:
        assert build_headline_summary("") == ""

    def test_all_empty_lines_returns_empty(self) -> None:
        assert build_headline_summary("\n\n\n") == ""
