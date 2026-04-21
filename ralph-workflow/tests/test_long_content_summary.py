"""Tests for long_content_summary — headline extraction for oversized content."""

from __future__ import annotations

from ralph.display.content_condenser import condense_content
from ralph.display.long_content_summary import (
    _AI_SUMMARY_MAX_CHARS,
    build_ai_summary,
    build_content_summary,
    build_headline_or_placeholder,
    build_headline_summary,
    set_ai_summary_hook,
    should_summarize,
)

_LONG = "x" * 5000
_SHORT = "x" * 100

_MAX_HEADLINE_LEN = 51  # max_chars=50 plus one ellipsis character
_MAX_CONTENT_SUMMARY_LEN = 201  # max_chars=200 plus one ellipsis character
_EXPECTED_2TUPLE = 2
_EXPECTED_4TUPLE = 4


class TestShouldSummarize:
    def test_default_summarize_when_env_unset(self) -> None:
        assert should_summarize("a" * 4100, {}) is True

    def test_summarize_disabled_by_kill_switch(self) -> None:
        for v in ("0", "false", "no", "off"):
            assert should_summarize("a" * 4100, {"RALPH_LONG_CONTENT_SUMMARY": v}) is False

    def test_summarize_enabled_explicit_above_threshold(self) -> None:
        assert should_summarize("a" * 4100, {"RALPH_LONG_CONTENT_SUMMARY": "1"}) is True

    def test_summarize_enabled_explicit_below_threshold(self) -> None:
        assert should_summarize("a" * 100, {"RALPH_LONG_CONTENT_SUMMARY": "1"}) is False

    def test_on_with_flag_true(self) -> None:
        assert should_summarize(_LONG, {"RALPH_LONG_CONTENT_SUMMARY": "true"}) is True

    def test_on_with_flag_yes(self) -> None:
        assert should_summarize(_LONG, {"RALPH_LONG_CONTENT_SUMMARY": "yes"}) is True

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


class TestAiSummaryHook:
    def setup_method(self) -> None:
        set_ai_summary_hook(None)

    def teardown_method(self) -> None:
        set_ai_summary_hook(None)

    def test_ai_summary_hook_invoked_when_configured(self) -> None:
        calls: list[str] = []

        def hook(text: str) -> str:
            calls.append(text)
            return "AI says: " + text[:10]

        set_ai_summary_hook(hook)
        long_text = "x" * 5000
        result = build_ai_summary(long_text, {"RALPH_LONG_CONTENT_AI_SUMMARY": "1"})
        assert len(calls) == 1
        assert calls[0] == long_text
        assert result is not None
        assert "AI says:" in result

    def test_ai_summary_hook_not_invoked_when_disabled(self) -> None:
        calls: list[str] = []

        def hook(text: str) -> str:
            calls.append(text)
            return "AI says something"

        set_ai_summary_hook(hook)
        result = build_ai_summary("x" * 5000, {})
        assert len(calls) == 0
        assert result is None

    def test_ai_summary_hook_error_is_swallowed(self) -> None:
        def hook(text: str) -> str:
            raise RuntimeError("hook failed")

        set_ai_summary_hook(hook)
        result = build_ai_summary("x" * 5000, {"RALPH_LONG_CONTENT_AI_SUMMARY": "1"})
        assert result is None

    def test_ai_summary_not_invoked_when_no_hook(self) -> None:
        result = build_ai_summary("x" * 5000, {"RALPH_LONG_CONTENT_AI_SUMMARY": "1"})
        assert result is None

    def test_ai_summary_capped_at_400_chars(self) -> None:
        def hook(text: str) -> str:
            return "x" * 500

        set_ai_summary_hook(hook)
        result = build_ai_summary("x" * 5000, {"RALPH_LONG_CONTENT_AI_SUMMARY": "1"})
        assert result is not None
        assert len(result) <= _AI_SUMMARY_MAX_CHARS + 1  # cap + ellipsis
        assert result.endswith("…")

    def test_ai_summary_not_invoked_below_threshold(self) -> None:
        calls: list[str] = []

        def hook(text: str) -> str:
            calls.append(text)
            return "summary"

        set_ai_summary_hook(hook)
        result = build_ai_summary("short text", {"RALPH_LONG_CONTENT_AI_SUMMARY": "1"})
        assert len(calls) == 0
        assert result is None


class TestCondenseContentSummaryContract:
    def test_2tuple_returned_when_summary_false(self) -> None:
        result = condense_content("x" * 6000)
        assert len(result) == _EXPECTED_2TUPLE
        visible, condensed = result
        assert isinstance(visible, str)
        assert isinstance(condensed, bool)

    def test_4tuple_returned_when_summary_true(self) -> None:
        result = condense_content("x" * 6000, summary=True)
        assert len(result) == _EXPECTED_4TUPLE
        visible, condensed, summary_line, ai_summary_line = result
        assert isinstance(visible, str)
        assert condensed is True
        assert summary_line is None or isinstance(summary_line, str)
        assert ai_summary_line is None or isinstance(ai_summary_line, str)

    def test_summary_none_for_short_text(self) -> None:
        result = condense_content("short", summary=True)
        _visible, condensed, summary_line, ai_summary_line = result
        assert condensed is False
        assert summary_line is None
        assert ai_summary_line is None

    def test_empty_text_summary_none(self) -> None:
        result = condense_content("", summary=True)
        assert result == ("", False, None, None)
