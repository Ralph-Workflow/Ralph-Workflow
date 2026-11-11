from __future__ import annotations

import time as real_time

import pytest
from loguru import logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import PostExitVerdict, PostExitWatchdog, TimeoutPolicy
from ralph.agents.timeout_clock import FakeClock

_POLL_UNTIL_TRUE = 3
_SIGNALS_ON_CALL_2 = 2


def _make_post_exit(
    parent_exit_grace: float = 5.0,
    descendant_wait: float = 30.0,
    descendant_poll: float = 0.5,
    process_exit_wait: float = 30.0,
    start: float = 0.0,
) -> tuple[PostExitWatchdog, FakeClock]:
    policy = TimeoutPolicy(
        idle_timeout_seconds=None,
        parent_exit_grace_seconds=parent_exit_grace,
        descendant_wait_timeout_seconds=descendant_wait,
        descendant_wait_poll_seconds=descendant_poll,
        process_exit_wait_seconds=process_exit_wait,
    )
    clock = FakeClock(start=start)
    return PostExitWatchdog(policy, clock), clock


def _terminal() -> AgentExecutionState:
    return AgentExecutionState.TERMINAL_COMPLETE


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def _resumable() -> AgentExecutionState:
    return AgentExecutionState.RESUMABLE_CONTINUE


def test_wait_for_process_exit_returns_immediately_when_already_exited() -> None:
    _watchdog, clock = _make_post_exit()
    call_count: list[int] = [0]

    def predicate() -> bool:
        call_count[0] += 1
        return True

    verdict = _watchdog.wait_for_process_exit(predicate)
    assert verdict == PostExitVerdict.CONTINUE
    assert call_count[0] == 1
    assert clock.monotonic() == 0.0


def test_wait_for_process_exit_polls_until_predicate_true() -> None:
    watchdog, clock = _make_post_exit(process_exit_wait=10.0, descendant_poll=0.5)
    ticks: list[int] = [0]

    def predicate() -> bool:
        ticks[0] += 1
        return ticks[0] >= _POLL_UNTIL_TRUE

    verdict = watchdog.wait_for_process_exit(predicate)
    assert verdict == PostExitVerdict.CONTINUE
    assert ticks[0] == _POLL_UNTIL_TRUE
    assert clock.monotonic() == pytest.approx(1.0, abs=0.001)


def test_wait_for_process_exit_fires_at_deadline() -> None:
    watchdog, clock = _make_post_exit(process_exit_wait=3.0, descendant_poll=0.5)

    def predicate() -> bool:
        return False

    verdict = watchdog.wait_for_process_exit(predicate)
    assert verdict == PostExitVerdict.FIRE_PROCESS_EXIT_HANG
    assert watchdog.last_verdict_reason == PostExitVerdict.FIRE_PROCESS_EXIT_HANG
    assert clock.monotonic() == pytest.approx(3.0, abs=0.001)


def test_wait_parent_exit_grace_signals_present_immediately() -> None:
    _watchdog, _clock = _make_post_exit()
    call_count: list[int] = [0]

    def classify() -> AgentExecutionState:
        call_count[0] += 1
        return AgentExecutionState.TERMINAL_COMPLETE

    verdict = _watchdog.wait_parent_exit_grace(classify)
    assert verdict == PostExitVerdict.SIGNALS_PRESENT
    assert call_count[0] == 1


def test_wait_parent_exit_grace_signals_appear_mid_window() -> None:
    watchdog, clock = _make_post_exit(parent_exit_grace=5.0, descendant_poll=0.5)
    ticks: list[int] = [0]

    def classify() -> AgentExecutionState:
        ticks[0] += 1
        if ticks[0] >= _POLL_UNTIL_TRUE:
            return AgentExecutionState.TERMINAL_COMPLETE
        return AgentExecutionState.RESUMABLE_CONTINUE

    verdict = watchdog.wait_parent_exit_grace(classify)
    assert verdict == PostExitVerdict.SIGNALS_PRESENT
    assert clock.monotonic() == pytest.approx(1.0, abs=0.001)


def test_wait_parent_exit_grace_children_appear_then_signals() -> None:
    watchdog, clock = _make_post_exit(parent_exit_grace=5.0, descendant_poll=0.5)
    ticks: list[int] = [0]

    def classify() -> AgentExecutionState:
        ticks[0] += 1
        if ticks[0] == 1:
            return AgentExecutionState.WAITING_ON_CHILD
        return AgentExecutionState.TERMINAL_COMPLETE

    verdict = watchdog.wait_parent_exit_grace(classify)
    assert verdict == PostExitVerdict.CHILDREN_ACTIVE
    assert clock.monotonic() == 0.0


def test_wait_parent_exit_grace_quiesces_no_signals() -> None:
    watchdog, clock = _make_post_exit(parent_exit_grace=5.0, descendant_poll=0.5)

    def classify() -> AgentExecutionState:
        return AgentExecutionState.RESUMABLE_CONTINUE

    verdict = watchdog.wait_parent_exit_grace(classify)
    assert verdict == PostExitVerdict.QUIESCED_NO_SIGNALS
    assert clock.monotonic() == pytest.approx(5.0, abs=0.001)


def test_wait_descendant_quiesce_signals_present() -> None:
    watchdog, clock = _make_post_exit(descendant_wait=30.0, descendant_poll=0.5)
    ticks: list[int] = [0]

    def classify() -> AgentExecutionState:
        ticks[0] += 1
        if ticks[0] >= _SIGNALS_ON_CALL_2:
            return AgentExecutionState.TERMINAL_COMPLETE
        return AgentExecutionState.WAITING_ON_CHILD

    verdict = watchdog.wait_descendant_quiesce(classify)
    assert verdict == PostExitVerdict.SIGNALS_PRESENT
    assert clock.monotonic() == pytest.approx(0.5, abs=0.001)


def test_wait_descendant_quiesce_quiesces_to_resumable() -> None:
    watchdog, clock = _make_post_exit(descendant_wait=30.0, descendant_poll=0.5)
    ticks: list[int] = [0]

    def classify() -> AgentExecutionState:
        ticks[0] += 1
        if ticks[0] >= _SIGNALS_ON_CALL_2:
            return AgentExecutionState.RESUMABLE_CONTINUE
        return AgentExecutionState.WAITING_ON_CHILD

    verdict = watchdog.wait_descendant_quiesce(classify)
    assert verdict == PostExitVerdict.QUIESCED_NO_SIGNALS
    assert clock.monotonic() == pytest.approx(0.5, abs=0.001)


def test_wait_descendant_quiesce_fires_when_persistent_waiting() -> None:
    watchdog, clock = _make_post_exit(descendant_wait=3.0, descendant_poll=0.5)

    def classify() -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD

    verdict = watchdog.wait_descendant_quiesce(classify)
    assert verdict == PostExitVerdict.FIRE_DESCENDANT_HANG
    assert watchdog.last_verdict_reason == PostExitVerdict.FIRE_DESCENDANT_HANG
    assert clock.monotonic() == pytest.approx(3.0, abs=0.001)


def test_post_exit_watchdog_uses_clock_only() -> None:
    original_monotonic = real_time.monotonic
    original_sleep = real_time.sleep
    calls: list[str] = []

    def tracking_monotonic() -> float:
        calls.append("monotonic")
        return original_monotonic()

    def tracking_sleep(s: float) -> None:
        calls.append(f"sleep:{s}")
        original_sleep(s)

    _watchdog, clock = _make_post_exit(parent_exit_grace=5.0, descendant_poll=0.5)

    object.__setattr__(clock, "_orig_monotonic", clock.monotonic)
    object.__setattr__(clock, "_orig_sleep", clock.sleep)

    for _ in range(10):
        clock.sleep(0.5)

    assert clock.monotonic() == pytest.approx(5.0, abs=0.001)


def test_post_exit_watchdog_validates_negative_poll() -> None:
    policy = TimeoutPolicy(
        idle_timeout_seconds=None,
        descendant_wait_poll_seconds=0.5,
        process_exit_wait_seconds=30.0,
    )
    clock = FakeClock()
    watchdog = PostExitWatchdog(policy, clock)
    assert watchdog is not None


def test_post_exit_watchdog_logs_with_correct_component() -> None:
    captured_messages: list[str] = []

    def _sink(message: object) -> None:
        captured_messages.append(str(message))

    sink_id = logger.add(
        _sink,
        level="WARNING",
        filter=lambda r: r["extra"].get("component") == "post_exit_watchdog",
    )
    try:
        watchdog, _clock = _make_post_exit(descendant_poll=0.5, process_exit_wait=1.0)
        watchdog.wait_for_process_exit(lambda: False)
    finally:
        logger.remove(sink_id)

    assert any("post_exit_watchdog" in msg for msg in captured_messages), (
        f"Expected log from post_exit_watchdog component, got: {captured_messages}"
    )


def test_descendant_hang_logs_distinct_reason() -> None:
    captured_messages: list[str] = []

    def _sink(message: object) -> None:
        captured_messages.append(str(message))

    sink_id = logger.add(
        _sink,
        level="WARNING",
        filter=lambda r: r["extra"].get("component") == "post_exit_watchdog",
    )
    try:
        watchdog, _clock = _make_post_exit(descendant_wait=1.0, descendant_poll=0.5)
        watchdog.wait_descendant_quiesce(lambda: AgentExecutionState.WAITING_ON_CHILD)
    finally:
        logger.remove(sink_id)

    assert any("descendant_hang" in msg for msg in captured_messages), (
        f"Expected 'descendant_hang' in log, got: {captured_messages}"
    )
    assert not any("children_persist_too_long" in msg for msg in captured_messages), (
        f"Must not log 'children_persist_too_long' from PostExitWatchdog, got: {captured_messages}"
    )
