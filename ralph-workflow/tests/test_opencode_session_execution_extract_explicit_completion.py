from __future__ import annotations

from ralph.agents.completion_signals import extract_explicit_completion

_DESCENDANT_WAIT_POLL_SECONDS = 0.5


class TestExtractExplicitCompletion:
    def test_detects_marker_in_raw_output(self) -> None:
        raw = [
            '{"type": "text", "content": "Working..."}',
            "Task declared complete: session_id=x, summary=done",
        ]
        assert extract_explicit_completion(raw) is True

    def test_returns_false_when_no_marker(self) -> None:
        raw = [
            '{"type": "text", "content": "Working..."}',
            '{"type": "tool_use", "tool": "read_file"}',
        ]
        assert extract_explicit_completion(raw) is False

    def test_returns_false_for_empty_output(self) -> None:
        assert extract_explicit_completion([]) is False
