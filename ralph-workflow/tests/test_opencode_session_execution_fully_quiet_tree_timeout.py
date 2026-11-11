from __future__ import annotations

from ralph.agents.execution_state import (
    AgentExecutionState,
    OpenCodeExecutionStrategy,
)
from ralph.process.liveness import FakeLivenessProbe
from tests.fake_handle import _FakeHandle

_DESCENDANT_WAIT_POLL_SECONDS = 0.5


class TestFullyQuietTreeTimeout:
    def test_multi_agent_tree_fully_quiet_triggers_timeout(self) -> None:
        strategy = OpenCodeExecutionStrategy()
        probe = FakeLivenessProbe(active=False)
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state != AgentExecutionState.WAITING_ON_CHILD, (
            "Fully-quiet tree must not report WAITING_ON_CHILD; "
            f"idle timeout must fire. Got: {state!r}"
        )
