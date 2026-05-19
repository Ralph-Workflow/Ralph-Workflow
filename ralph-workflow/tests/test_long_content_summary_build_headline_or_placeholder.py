"""Tests for long_content_summary — headline extraction for oversized content."""

from __future__ import annotations

from ralph.display.long_content_summary import (
    build_content_summary,
    build_headline_or_placeholder,
)

_LONG = "x" * 5000
_SHORT = "x" * 100

_MAX_HEADLINE_LEN = 51  # max_chars=50 plus one ellipsis character
_MAX_CONTENT_SUMMARY_LEN = 201  # max_chars=200 plus one ellipsis character
_EXPECTED_2TUPLE = 2
_EXPECTED_4TUPLE = 4


class TestBuildHeadlineOrPlaceholder:
    def test_returns_headline_when_content_available(self) -> None:
        assert build_headline_or_placeholder("First sentence. More text.") == "First sentence."

    def test_returns_placeholder_when_all_empty_lines(self) -> None:
        assert build_headline_or_placeholder("\n\n   \n") == "(no headline available)"

    def test_returns_placeholder_when_empty_string(self) -> None:
        assert build_headline_or_placeholder("") == "(no headline available)"

    def test_markdown_only_stripped_to_empty_returns_placeholder(self) -> None:
        assert build_headline_or_placeholder("# \n# \n") == "(no headline available)"

    def test_truncates_to_max_chars(self) -> None:
        text = "a" * 200
        result = build_headline_or_placeholder(text, max_chars=50)
        assert result.endswith("…")

    def test_empty_headline_falls_back_to_placeholder(self) -> None:
        # Text whose splitlines all yield empty stripped lines
        empty_text = "\n\n\n"
        assert build_content_summary(empty_text) == ""
        assert build_headline_or_placeholder(empty_text) == "(no headline available)"
