from __future__ import annotations

from ralph.display.long_content_summary import (
    should_summarize,
)

_LONG = "x" * 5000
_SHORT = "x" * 100

_MAX_HEADLINE_LEN = 51
_MAX_CONTENT_SUMMARY_LEN = 201
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
        text = "x" * 4001
        assert should_summarize(text, {"RALPH_LONG_CONTENT_SUMMARY": "1"}) is True

    def test_off_at_threshold(self) -> None:
        text = "x" * 4000
        assert should_summarize(text, {"RALPH_LONG_CONTENT_SUMMARY": "1"}) is False
