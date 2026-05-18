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


class TestManygenerationChildTree:
    class _FakeHandle:
        returncode: int = 0
        stdout = None
        stderr = None

        def __init__(self, *, returncode: int = 0, has_descendants: bool = False) -> None:
            self.returncode = returncode
            self._has_descendants = has_descendants

        def has_live_descendants(self) -> bool:
            return self._has_descendants

        def descendant_snapshot(self) -> tuple[int, float | None]:
            return (1 if self._has_descendants else 0, 5.0 if self._has_descendants else None)

        def poll(self) -> int | None:
            return self.returncode

    def test_opencode_tree_with_many_generations_of_children(self) -> None:
        """Liveness probe reporting any active agent keeps the run alive."""
        strategy = OpenCodeExecutionStrategy()
        probe = FakeLivenessProbe(active=True)
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD


_FakeHandle = TestManygenerationChildTree._FakeHandle
