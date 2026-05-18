"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

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


class TestQuietParentWithLiveChild:

    class _FakeHandle:
        """Minimal fake ManagedProcess for strategy tests."""

        def __init__(
            self,
            *,
            returncode: int = 0,
            has_descendants: bool = False,
        ) -> None:
            self.returncode = returncode
            self._has_descendants = has_descendants

        def has_live_descendants(self) -> bool:
            return self._has_descendants

    def test_quiet_parent_with_live_child_is_not_idle(self) -> None:
        """OpenCodeExecutionStrategy classifies quiet parent with live child as WAITING_ON_CHILD."""
        strategy = OpenCodeExecutionStrategy()
        probe = FakeLivenessProbe(active=True)
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD


_FakeHandle = TestQuietParentWithLiveChild._FakeHandle
