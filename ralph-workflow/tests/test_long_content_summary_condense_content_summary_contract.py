"""Tests for long_content_summary — headline extraction for oversized content."""

from __future__ import annotations

from ralph.display.content_condenser import CondenseOptions, condense_content

_LONG = "x" * 5000
_SHORT = "x" * 100

_MAX_HEADLINE_LEN = 51  # max_chars=50 plus one ellipsis character
_MAX_CONTENT_SUMMARY_LEN = 201  # max_chars=200 plus one ellipsis character
_EXPECTED_2TUPLE = 2
_EXPECTED_4TUPLE = 4


class TestCondenseContentSummaryContract:
    def test_2tuple_returned_when_summary_false(self) -> None:
        result = condense_content("x" * 6000)
        assert len(result) == _EXPECTED_2TUPLE
        visible = result[0]
        condensed = result[1]
        assert isinstance(visible, str)
        assert isinstance(condensed, bool)

    def test_4tuple_returned_when_summary_true(self) -> None:
        result = condense_content("x" * 6000, options=CondenseOptions(summary=True))
        assert len(result) == _EXPECTED_4TUPLE
        visible = result[0]
        condensed = result[1]
        summary_line = result[2]
        ai_summary_line = result[3]
        assert isinstance(visible, str)
        assert condensed is True
        assert summary_line is None or isinstance(summary_line, str)
        assert ai_summary_line is None or isinstance(ai_summary_line, str)

    def test_summary_none_for_short_text(self) -> None:
        result = condense_content("short", options=CondenseOptions(summary=True))
        _visible = result[0]
        condensed = result[1]
        summary_line = result[2] if len(result) == _EXPECTED_4TUPLE else None
        ai_summary_line = result[3] if len(result) == _EXPECTED_4TUPLE else None
        assert condensed is False
        assert summary_line is None
        assert ai_summary_line is None

    def test_empty_text_summary_none(self) -> None:
        result = condense_content("", options=CondenseOptions(summary=True))
        assert result == ("", False, None, None)
