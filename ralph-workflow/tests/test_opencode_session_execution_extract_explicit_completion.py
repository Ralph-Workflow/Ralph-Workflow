"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

from ralph.agents.completion_signals import extract_explicit_completion
from ralph.agents.invoke import (
    CompletionCheckOptions,
    check_process_result,
)

# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5

# Local aliases: tests call the same public functions but under the private-looking names
# that were used when this module was monolithic (pre-package split).
_check_process_result = check_process_result
_CompletionCheckOptions = CompletionCheckOptions


class TestExtractExplicitCompletion:
    """extract_explicit_completion scans raw NDJSON output for the declare_complete marker."""

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
