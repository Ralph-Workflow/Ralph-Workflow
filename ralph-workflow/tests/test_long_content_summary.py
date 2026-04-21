"""Tests for long_content_summary — opt-in headline extraction for oversized content."""

from __future__ import annotations

from ralph.display.content_condenser import condense_content
from ralph.display.long_content_summary import build_headline_summary, should_summarize

_LONG = "x" * 5000
_SHORT = "x" * 100

_MAX_HEADLINE_LEN = 51  # max_chars=50 plus one ellipsis character
_EXPECTED_2TUPLE = 2
_EXPECTED_3TUPLE = 3


class TestShouldSummarize:
    def test_off_by_default_empty_env(self) -> None:
        assert should_summarize(_LONG, {}) is False

    def test_off_when_flag_not_set(self) -> None:
        assert should_summarize(_LONG, {"OTHER": "1"}) is False

    def test_on_with_flag_1(self) -> None:
        assert should_summarize(_LONG, {"RALPH_LONG_CONTENT_SUMMARY": "1"}) is True

    def test_on_with_flag_true(self) -> None:
        assert should_summarize(_LONG, {"RALPH_LONG_CONTENT_SUMMARY": "true"}) is True

    def test_on_with_flag_yes(self) -> None:
        assert should_summarize(_LONG, {"RALPH_LONG_CONTENT_SUMMARY": "yes"}) is True

    def test_off_with_unknown_flag_value(self) -> None:
        assert should_summarize(_LONG, {"RALPH_LONG_CONTENT_SUMMARY": "enabled"}) is False

    def test_off_when_text_below_threshold(self) -> None:
        assert should_summarize(_SHORT, {"RALPH_LONG_CONTENT_SUMMARY": "1"}) is False

    def test_on_exactly_at_threshold_boundary(self) -> None:
        # 4001 chars > 4000 threshold
        text = "x" * 4001
        assert should_summarize(text, {"RALPH_LONG_CONTENT_SUMMARY": "1"}) is True

    def test_off_at_threshold(self) -> None:
        # exactly 4000 chars — not > 4000
        text = "x" * 4000
        assert should_summarize(text, {"RALPH_LONG_CONTENT_SUMMARY": "1"}) is False


class TestBuildHeadlineSummary:
    def test_first_nonempty_line_returned(self) -> None:
        text = "First line\nSecond line\nThird line"
        assert build_headline_summary(text) == "First line"

    def test_empty_first_lines_skipped(self) -> None:
        text = "\n\nActual content\nMore"
        assert build_headline_summary(text) == "Actual content"

    def test_markdown_heading_prefix_stripped(self) -> None:
        text = "# My Heading\nBody text"
        assert build_headline_summary(text) == "My Heading"

    def test_double_heading_prefix_stripped(self) -> None:
        text = "## Sub Heading\nBody text"
        assert build_headline_summary(text) == "Sub Heading"

    def test_blockquote_prefix_stripped(self) -> None:
        text = "> Quoted line\nOther"
        assert build_headline_summary(text) == "Quoted line"

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

    def test_leading_spaces_stripped(self) -> None:
        text = "   trimmed content"
        assert build_headline_summary(text) == "trimmed content"


class TestCondenseContentSummaryContract:
    def test_2tuple_returned_when_summary_false(self) -> None:
        result = condense_content("x" * 6000)
        assert len(result) == _EXPECTED_2TUPLE
        visible, condensed = result
        assert isinstance(visible, str)
        assert isinstance(condensed, bool)

    def test_3tuple_returned_when_summary_true(self) -> None:
        result = condense_content("x" * 6000, summary=True)
        assert len(result) == _EXPECTED_3TUPLE
        visible, condensed, summary_line = result
        assert isinstance(visible, str)
        assert condensed is True
        assert summary_line is None or isinstance(summary_line, str)

    def test_summary_none_for_short_text(self) -> None:
        result = condense_content("short", summary=True)
        _visible, condensed, summary_line = result
        assert condensed is False
        assert summary_line is None

    def test_summary_none_when_env_flag_not_set(self) -> None:
        # os.environ does not have RALPH_LONG_CONTENT_SUMMARY in test env
        result = condense_content("x" * 6000, summary=True)
        _, _, summary_line = result
        # Without env flag set, summary_line must be None
        assert summary_line is None

    def test_empty_text_summary_none(self) -> None:
        result = condense_content("", summary=True)
        assert result == ("", False, None)
