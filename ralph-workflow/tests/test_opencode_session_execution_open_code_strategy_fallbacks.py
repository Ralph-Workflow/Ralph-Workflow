"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    AgentExecutionState,
    OpenCodeExecutionStrategy,
)
from ralph.agents.invoke import (
    CompletionCheckOptions,
    check_process_result,
)

if TYPE_CHECKING:
    from ralph.process.liveness import FakeLivenessProbe

# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5

# Local aliases: tests call the same public functions but under the private-looking names
# that were used when this module was monolithic (pre-package split).
_check_process_result = check_process_result
_CompletionCheckOptions = CompletionCheckOptions


class TestOpenCodeStrategyFallbacks:
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

    def test_classify_quiet_probe_exception_falls_back_to_descendants(self) -> None:
        class _RaisingProbe:
            def any_agent_active(self, label_prefix: str) -> bool:
                del label_prefix
                raise RuntimeError("boom")

        strategy = OpenCodeExecutionStrategy(label_scope="run-scope")
        handle = _FakeHandle(has_descendants=True)

        state = strategy.classify_quiet(handle, cast("FakeLivenessProbe", _RaisingProbe()))

        assert state == AgentExecutionState.WAITING_ON_CHILD

    def test_classify_exit_probe_exception_falls_back_to_descendants(self) -> None:
        class _RaisingProbe:
            def any_agent_active(self, label_prefix: str) -> bool:
                del label_prefix
                raise RuntimeError("boom")

        strategy = OpenCodeExecutionStrategy(label_scope="run-scope")
        handle = _FakeHandle(returncode=0, has_descendants=True)
        signals = CompletionSignals(False, False, ())

        state = strategy.classify_exit(
            handle,
            signals,
            liveness_probe=cast("FakeLivenessProbe", _RaisingProbe()),
        )

        assert state == AgentExecutionState.WAITING_ON_CHILD

    def test_classify_exit_probe_exception_without_descendants_is_resumable(self) -> None:
        class _RaisingProbe:
            def any_agent_active(self, label_prefix: str) -> bool:
                del label_prefix
                raise RuntimeError("boom")

        strategy = OpenCodeExecutionStrategy(label_scope="run-scope")
        handle = _FakeHandle(returncode=0, has_descendants=False)
        signals = CompletionSignals(False, False, ())

        state = strategy.classify_exit(
            handle,
            signals,
            liveness_probe=cast("FakeLivenessProbe", _RaisingProbe()),
        )

        assert state == AgentExecutionState.RESUMABLE_CONTINUE


_FakeHandle = TestOpenCodeStrategyFallbacks._FakeHandle
