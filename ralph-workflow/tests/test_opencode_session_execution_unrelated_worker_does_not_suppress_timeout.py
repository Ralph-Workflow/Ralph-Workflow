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


class TestUnrelatedWorkerDoesNotSuppressTimeout:
    """Session-scoped liveness: unrelated agent: workers must not keep this run alive.

    OpenCodeExecutionStrategy accepts an optional ``label_scope`` that narrows
    the Ralph-tracked liveness check to ``agent:{label_scope}:``. When no scope
    is available, the strategy must ignore Ralph-tracked `agent:*` labels and
    rely on OS-level descendant detection instead.
    """



    def test_unrelated_agent_worker_does_not_suppress_timeout(self) -> None:
        """Unrelated agent:other-session worker does not keep scoped run alive."""
        strategy = OpenCodeExecutionStrategy(label_scope="my-session")
        # Probe: only an unrelated worker with a different session label is active.
        probe = FakeLivenessProbe(active_labels=frozenset({"agent:other-session:worker1"}))
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        # "agent:other-session:worker1" does NOT start with "agent:my-session:"
        # so the scoped check returns False → ACTIVE (timeout may fire)
        assert state == AgentExecutionState.ACTIVE, (
            f"Unrelated worker must not suppress timeout for scoped run; got {state!r}"
        )

    def test_related_agent_worker_keeps_scoped_run_alive(self) -> None:
        """Related agent:my-session: worker keeps the scoped run in WAITING_ON_CHILD."""
        strategy = OpenCodeExecutionStrategy(label_scope="my-session")
        probe = FakeLivenessProbe(active_labels=frozenset({"agent:my-session:worker1"}))
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        # "agent:my-session:worker1" starts with "agent:my-session:" → WAITING_ON_CHILD
        assert state == AgentExecutionState.WAITING_ON_CHILD, (
            f"Related worker must keep the run in WAITING_ON_CHILD; got {state!r}"
        )

    def test_unscoped_run_uses_descendant_and_lease_evidence(self) -> None:
        """Unscoped: empty registry + no descendants -> ACTIVE."""
        strategy = OpenCodeExecutionStrategy()  # no label_scope
        # FakeLivenessProbe with active=False: child_snapshot('') returns has_process=False
        probe = FakeLivenessProbe(active=False)
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.ACTIVE, (
            f"Unscoped empty registry + no descendants must be ACTIVE; got {state!r}"
        )


