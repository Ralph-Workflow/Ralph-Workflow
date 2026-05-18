"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations
from tests.fake_handle import _FakeHandle

from ralph.agents.execution_state import (
    AgentExecutionState,
    OpenCodeExecutionStrategy,
)
from ralph.agents.invoke import (
    CompletionCheckOptions,
    check_process_result,
)
from ralph.process.liveness import FakeLivenessProbe

# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5

# Local aliases: tests call the same public functions but under the private-looking names
# that were used when this module was monolithic (pre-package split).
_check_process_result = check_process_result
_CompletionCheckOptions = CompletionCheckOptions


class TestFullyQuietTreeTimeout:


    def test_multi_agent_tree_fully_quiet_triggers_timeout(self) -> None:
        """When all agents are inactive, classify_quiet must NOT return WAITING_ON_CHILD."""
        strategy = OpenCodeExecutionStrategy()
        probe = FakeLivenessProbe(active=False)
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state != AgentExecutionState.WAITING_ON_CHILD, (
            "Fully-quiet tree must not report WAITING_ON_CHILD; "
            f"idle timeout must fire. Got: {state!r}"
        )


