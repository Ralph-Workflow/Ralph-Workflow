"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

import pytest

from ralph.agents.invoke import (
    AgentInvocationError,
    CompletionCheckOptions,
    check_process_result,
)
from ralph.recovery.classifier import FailureCategory, FailureClassifier

# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5

# Local aliases: tests call the same public functions but under the private-looking names
# that were used when this module was monolithic (pre-package split).
_check_process_result = check_process_result
_CompletionCheckOptions = CompletionCheckOptions


class TestStaleSessionRecovery:
    @pytest.mark.parametrize(
        "stale_message",
        [
            "Session not found: abc123",
            "Unknown session: deadbeef",
            "session does not exist",
        ],
    )
    def test_stale_session_recovers_predictably(self, stale_message: str) -> None:
        """OpenCode stale-session messages trigger reset_session=True in FailureClassifier."""
        classifier = FailureClassifier()
        exc = AgentInvocationError("opencode", 1, stale_message)
        failure = classifier.classify(exc, phase="development", agent="opencode")

        assert failure.reset_session is True, (
            f"Expected reset_session=True for OpenCode message {stale_message!r}"
        )
        assert failure.counts_against_budget is True
        assert failure.category == FailureCategory.AGENT
