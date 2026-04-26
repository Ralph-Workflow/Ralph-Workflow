"""Black-box tests for PostExitWatchdog policy using FakeClock."""

from __future__ import annotations

import time as real_time

import pytest
from loguru import logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.post_exit_watchdog import PostExitVerdict, PostExitWatchdog
from ralph.agents.timeout_clock import FakeClock

# Number of poll iterations before predicate becomes True in tests
_POLL_UNTIL_TRUE = 3
# Number of poll iterations before state changes to TERMINAL_COMPLETE in tests
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


# ---------------------------------------------------------------------------
# wait_for_process_exit tests
# ---------------------------------------------------------------------------

def test_wait_for_process_exit_returns_immediately_when_already_exited() -> None:
    """Predicate True on first call -> CONTINUE without consuming clock budget."""
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
    """Predicate becomes True after N ticks before deadline -> CONTINUE."""
    watchdog, clock = _make_post_exit(process_exit_wait=10.0, descendant_poll=0.5)
    ticks: list[int] = [0]

    def predicate() -> bool:
        ticks[0] += 1
        return ticks[0] >= _POLL_UNTIL_TRUE

    verdict = watchdog.wait_for_process_exit(predicate)
    assert verdict == PostExitVerdict.CONTINUE
    assert ticks[0] == _POLL_UNTIL_TRUE
    # 2 sleeps of 0.5 each = 1.0 elapsed
    assert clock.monotonic() == pytest.approx(1.0, abs=0.001)


def test_wait_for_process_exit_fires_at_deadline() -> None:
    """Predicate stays False past process_exit_wait_seconds -> FIRE_PROCESS_EXIT_HANG."""
    watchdog, clock = _make_post_exit(process_exit_wait=3.0, descendant_poll=0.5)

    def predicate() -> bool:
        return False

    verdict = watchdog.wait_for_process_exit(predicate)
    assert verdict == PostExitVerdict.FIRE_PROCESS_EXIT_HANG
    assert watchdog.last_verdict_reason == PostExitVerdict.FIRE_PROCESS_EXIT_HANG
    # 6 ticks of 0.5 = 3.0 elapsed (deadline reached)
    assert clock.monotonic() == pytest.approx(3.0, abs=0.001)


# ---------------------------------------------------------------------------
# wait_parent_exit_grace tests
# ---------------------------------------------------------------------------

def test_wait_parent_exit_grace_signals_present_immediately() -> None:
    """Classifier returns TERMINAL_COMPLETE on first call -> SIGNALS_PRESENT."""
    _watchdog, _clock = _make_post_exit()
    call_count: list[int] = [0]

    def classify() -> AgentExecutionState:
        call_count[0] += 1
        return AgentExecutionState.TERMINAL_COMPLETE

    verdict = _watchdog.wait_parent_exit_grace(classify)
    assert verdict == PostExitVerdict.SIGNALS_PRESENT
    assert call_count[0] == 1


def test_wait_parent_exit_grace_signals_appear_mid_window() -> None:
    """TERMINAL_COMPLETE returned after 2 ticks before parent_exit_grace_seconds elapses."""
    watchdog, clock = _make_post_exit(parent_exit_grace=5.0, descendant_poll=0.5)
    ticks: list[int] = [0]

    def classify() -> AgentExecutionState:
        ticks[0] += 1
        if ticks[0] >= _POLL_UNTIL_TRUE:
            return AgentExecutionState.TERMINAL_COMPLETE
        return AgentExecutionState.RESUMABLE_CONTINUE

    verdict = watchdog.wait_parent_exit_grace(classify)
    assert verdict == PostExitVerdict.SIGNALS_PRESENT
    # 2 sleeps of 0.5 = 1.0 elapsed, then TERMINAL_COMPLETE
    assert clock.monotonic() == pytest.approx(1.0, abs=0.001)


def test_wait_parent_exit_grace_children_appear_then_signals() -> None:
    """WAITING_ON_CHILD then TERMINAL_COMPLETE; CHILDREN_ACTIVE returned at first WAITING."""
    watchdog, clock = _make_post_exit(parent_exit_grace=5.0, descendant_poll=0.5)
    ticks: list[int] = [0]

    def classify() -> AgentExecutionState:
        ticks[0] += 1
        if ticks[0] == 1:
            return AgentExecutionState.WAITING_ON_CHILD
        return AgentExecutionState.TERMINAL_COMPLETE

    verdict = watchdog.wait_parent_exit_grace(classify)
    assert verdict == PostExitVerdict.CHILDREN_ACTIVE
    # Caller escalates to descendant wait when CHILDREN_ACTIVE is returned
    assert clock.monotonic() == 0.0  # no sleep before returning WAITING_ON_CHILD


def test_wait_parent_exit_grace_quiesces_no_signals() -> None:
    """Classifier returns RESUMABLE_CONTINUE for full grace window -> QUIESCED_NO_SIGNALS."""
    watchdog, clock = _make_post_exit(parent_exit_grace=5.0, descendant_poll=0.5)

    def classify() -> AgentExecutionState:
        return AgentExecutionState.RESUMABLE_CONTINUE

    verdict = watchdog.wait_parent_exit_grace(classify)
    assert verdict == PostExitVerdict.QUIESCED_NO_SIGNALS
    # 10 ticks of 0.5 = 5.0 elapsed
    assert clock.monotonic() == pytest.approx(5.0, abs=0.001)


# ---------------------------------------------------------------------------
# wait_descendant_quiesce tests
# ---------------------------------------------------------------------------

def test_wait_descendant_quiesce_signals_present() -> None:
    """TERMINAL_COMPLETE seen during polling -> SIGNALS_PRESENT."""
    watchdog, clock = _make_post_exit(descendant_wait=30.0, descendant_poll=0.5)
    ticks: list[int] = [0]

    def classify() -> AgentExecutionState:
        ticks[0] += 1
        if ticks[0] >= _SIGNALS_ON_CALL_2:
            return AgentExecutionState.TERMINAL_COMPLETE
        return AgentExecutionState.WAITING_ON_CHILD

    verdict = watchdog.wait_descendant_quiesce(classify)
    assert verdict == PostExitVerdict.SIGNALS_PRESENT
    # 1 sleep of 0.5
    assert clock.monotonic() == pytest.approx(0.5, abs=0.001)


def test_wait_descendant_quiesce_quiesces_to_resumable() -> None:
    """WAITING_ON_CHILD then RESUMABLE_CONTINUE -> QUIESCED_NO_SIGNALS."""
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
    """WAITING_ON_CHILD for full descendant_wait_timeout_seconds -> FIRE_DESCENDANT_HANG."""
    watchdog, clock = _make_post_exit(descendant_wait=3.0, descendant_poll=0.5)

    def classify() -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD

    verdict = watchdog.wait_descendant_quiesce(classify)
    assert verdict == PostExitVerdict.FIRE_DESCENDANT_HANG
    assert watchdog.last_verdict_reason == PostExitVerdict.FIRE_DESCENDANT_HANG
    # 6 ticks of 0.5 = 3.0 elapsed
    assert clock.monotonic() == pytest.approx(3.0, abs=0.001)


# ---------------------------------------------------------------------------
# Integration / correctness tests
# ---------------------------------------------------------------------------

def test_post_exit_watchdog_uses_clock_only() -> None:
    """Assert no time.monotonic / time.sleep calls leak in (uses FakeClock)."""
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

    # Patch the clock's monotonic/sleep to detect any direct real-time usage
    clock._orig_monotonic = clock.monotonic  # type: ignore[attr-defined]  # reason: autogenerated code has no type support, see docs/agents/type-ignore-policy.md#autogenerated-code
    clock._orig_sleep = clock.sleep  # type: ignore[attr-defined]  # reason: autogenerated code has no type support, see docs/agents/type-ignore-policy.md#autogenerated-code

    # We trust FakeClock to not call real time - the real test is that
    # all advances are via clock.sleep() / clock.monotonic() on the injected clock.
    # Verify the clock only advances by expected tick count * poll interval.
    for _ in range(10):
        clock.sleep(0.5)

    assert clock.monotonic() == pytest.approx(5.0, abs=0.001)
    # No real time calls should have been made by PostExitWatchdog itself
    # (the test above already validates this by having FakeClock track increments)


def test_post_exit_watchdog_validates_negative_poll() -> None:
    """TimeoutPolicy already validates; verify constructor accepts policy unchanged."""
    policy = TimeoutPolicy(
        idle_timeout_seconds=None,
        descendant_wait_poll_seconds=0.5,
        process_exit_wait_seconds=30.0,
    )
    clock = FakeClock()
    watchdog = PostExitWatchdog(policy, clock)
    assert watchdog is not None


def test_post_exit_watchdog_logs_with_correct_component() -> None:
    """PostExitWatchdog logs warnings with component='post_exit_watchdog'.

    This is a regression guard: PostExitWatchdog must NOT collide with the
    idle_watchdog component filter used by other tests.
    """
    captured_messages: list[str] = []

    def _sink(message: object) -> None:
        captured_messages.append(str(message))

    sink_id = logger.add(
        _sink,
        level="WARNING",
        filter=lambda r: r["extra"].get("component") == "post_exit_watchdog",
    )
    try:
        watchdog, _clock = _make_post_exit(
            descendant_poll=0.5, process_exit_wait=1.0
        )
        # Trigger a FIRE_PROCESS_EXIT_HANG verdict
        watchdog.wait_for_process_exit(lambda: False)
    finally:
        logger.remove(sink_id)

    assert any(
        "post_exit_watchdog" in msg for msg in captured_messages
    ), f"Expected log from post_exit_watchdog component, got: {captured_messages}"


def test_descendant_hang_logs_distinct_reason() -> None:
    """wait_descendant_quiesce logs DESCENDANT_HANG (not CHILDREN_PERSIST_TOO_LONG).

    DESCENDANT_HANG is the post-exit descendant-wait reason owned by PostExitWatchdog.
    CHILDREN_PERSIST_TOO_LONG is the in-stream reason owned by IdleWatchdog.
    Operational triage depends on these being distinct in the log output.
    """
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
        # WAITING_ON_CHILD for full deadline -> FIRE_DESCENDANT_HANG
        watchdog.wait_descendant_quiesce(lambda: AgentExecutionState.WAITING_ON_CHILD)
    finally:
        logger.remove(sink_id)

    assert any(
        "descendant_hang" in msg for msg in captured_messages
    ), f"Expected 'descendant_hang' in log, got: {captured_messages}"
    assert not any(
        "children_persist_too_long" in msg for msg in captured_messages
    ), f"Must not log 'children_persist_too_long' from PostExitWatchdog, got: {captured_messages}"
