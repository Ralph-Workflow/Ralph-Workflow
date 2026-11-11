from __future__ import annotations

from ralph.agents.execution_state import (
    AgentExecutionState,
    OpenCodeExecutionStrategy,
)
from ralph.process.liveness import FakeLivenessProbe
from tests.fake_handle import _FakeHandle

_DESCENDANT_WAIT_POLL_SECONDS = 0.5


class TestQuietParentWithLiveChild:
    def test_quiet_parent_with_live_child_is_not_idle(self) -> None:
        strategy = OpenCodeExecutionStrategy()
        probe = FakeLivenessProbe(active=True)
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD

    def test_opencode_regression_open_step_defers_idle_until_step_finishes(self) -> None:
        strategy = OpenCodeExecutionStrategy()
        probe = FakeLivenessProbe(active=False)
        handle = _FakeHandle(has_descendants=False)

        strategy.observe_line('{"type":"step_start","part":{"type":"step-start"}}')

        assert strategy.classify_quiet(handle, probe) == AgentExecutionState.WAITING_ON_CHILD

        strategy.observe_line('{"type":"step_finish","part":{"type":"step-finish"}}')

        assert strategy.classify_quiet(handle, probe) == AgentExecutionState.ACTIVE


def _frame(event_type: str, message_id: str | None) -> str:
    part = f'"messageID":"{message_id}"' if message_id is not None else '"type":"step-start"'
    return f'{{"type":"{event_type}","sessionID":"ses_parent","part":{{{part}}}}}'


def test_opencode_repeated_step_start_for_one_message_closes_with_one_step_finish() -> None:
    strategy = OpenCodeExecutionStrategy()
    probe = FakeLivenessProbe(active=False)
    handle = _FakeHandle(has_descendants=False)

    strategy.observe_line(_frame("step_start", "msg_1"))
    strategy.observe_line(_frame("step_start", "msg_1"))
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.WAITING_ON_CHILD

    strategy.observe_line(_frame("step_finish", "msg_1"))
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.ACTIVE


def test_opencode_stream_end_releases_every_open_step() -> None:
    strategy = OpenCodeExecutionStrategy()
    probe = FakeLivenessProbe(active=False)
    handle = _FakeHandle(has_descendants=False)

    strategy.observe_line(_frame("step_start", "msg_1"))
    strategy.observe_line(_frame("step_start", None))
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.WAITING_ON_CHILD

    strategy.observe_stream_end()
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.ACTIVE

    strategy.observe_line(_frame("step_finish", "msg_1"))
    strategy.observe_line(_frame("step_finish", None))
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.ACTIVE, (
        "late finish frames after EOF must not underflow the open set"
    )
