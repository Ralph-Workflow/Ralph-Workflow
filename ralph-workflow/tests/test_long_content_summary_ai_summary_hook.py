"""Tests for long_content_summary — headline extraction for oversized content."""

from __future__ import annotations

from ralph.display.long_content_summary import (
    AI_SUMMARY_MAX_CHARS,
    build_ai_summary,
    set_ai_summary_hook,
)

_LONG = "x" * 5000
_SHORT = "x" * 100

_MAX_HEADLINE_LEN = 51  # max_chars=50 plus one ellipsis character
_MAX_CONTENT_SUMMARY_LEN = 201  # max_chars=200 plus one ellipsis character
_EXPECTED_2TUPLE = 2
_EXPECTED_4TUPLE = 4


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
        assert len(result) <= AI_SUMMARY_MAX_CHARS + 1  # cap + ellipsis
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
