from __future__ import annotations

from ralph.agents.execution_state import (
    AgentExecutionState,
    OpenCodeExecutionStrategy,
)
from ralph.process.liveness import FakeLivenessProbe
from tests.fake_handle import _FakeHandle

_DESCENDANT_WAIT_POLL_SECONDS = 0.5


class TestUnrelatedWorkerDoesNotSuppressTimeout:
    def test_unrelated_agent_worker_does_not_suppress_timeout(self) -> None:
        strategy = OpenCodeExecutionStrategy(label_scope="my-session")
        probe = FakeLivenessProbe(active_labels=frozenset({"agent:other-session:worker1"}))
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.ACTIVE, (
            f"Unrelated worker must not suppress timeout for scoped run; got {state!r}"
        )

    def test_related_agent_worker_keeps_scoped_run_alive(self) -> None:
        strategy = OpenCodeExecutionStrategy(label_scope="my-session")
        probe = FakeLivenessProbe(active_labels=frozenset({"agent:my-session:worker1"}))
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD, (
            f"Related worker must keep the run in WAITING_ON_CHILD; got {state!r}"
        )

    def test_unscoped_run_uses_descendant_and_lease_evidence(self) -> None:
        strategy = OpenCodeExecutionStrategy()
        probe = FakeLivenessProbe(active=False)
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.ACTIVE, (
            f"Unscoped empty registry + no descendants must be ACTIVE; got {state!r}"
        )
