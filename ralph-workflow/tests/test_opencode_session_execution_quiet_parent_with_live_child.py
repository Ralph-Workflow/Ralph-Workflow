"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

from ralph.agents.execution_state import (
    AgentExecutionState,
    OpenCodeExecutionStrategy,
)
from ralph.process.liveness import FakeLivenessProbe
from tests.fake_handle import _FakeHandle

# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5

# Local aliases: tests call the same public functions but under the private-looking names
# that were used when this module was monolithic (pre-package split).


class TestQuietParentWithLiveChild:
    def test_quiet_parent_with_live_child_is_not_idle(self) -> None:
        """OpenCodeExecutionStrategy classifies quiet parent with live child as WAITING_ON_CHILD."""
        strategy = OpenCodeExecutionStrategy()
        probe = FakeLivenessProbe(active=True)
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD

    def test_opencode_regression_open_step_defers_idle_until_step_finishes(self) -> None:
        """S-4: an active OpenCode turn remains live while native task output is buffered."""
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
    """A retried model call re-emits ``step_start`` for the SAME message.

    Captured from OpenCode's own store (message ``DoXp1777``: two
    ``step-start`` parts, one ``step-finish``). Counting frames left the
    turn open forever; the open set must be keyed by message.
    """
    strategy = OpenCodeExecutionStrategy()
    probe = FakeLivenessProbe(active=False)
    handle = _FakeHandle(has_descendants=False)

    strategy.observe_line(_frame("step_start", "msg_1"))
    strategy.observe_line(_frame("step_start", "msg_1"))
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.WAITING_ON_CHILD

    strategy.observe_line(_frame("step_finish", "msg_1"))
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.ACTIVE


def test_opencode_stream_end_releases_every_open_step() -> None:
    """Once stdout hit EOF no buffered ``task`` frame can still arrive.

    OpenCode exits its final turn without a trailing ``step_finish`` on
    stdout; a conflict-resolution drain that keeps asking ``classify_quiet``
    after the process exited must not be told to wait for a dead parent.
    """
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
