from __future__ import annotations

import pytest

from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy
from ralph.agents.invoke import BrokenAgentExitError, check_broken_agent_timer
from ralph.agents.timeout_clock import FakeClock
from ralph.process.manager import ManagedProcess
from ralph.timeout_defaults import (
    BROKEN_AGENT_EXIT_SETTLE_SECONDS,
    BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
    NO_OUTPUT_AT_START_SECONDS,
)


class _ExitedHandle(ManagedProcess):
    @property
    def pid(self) -> int:
        return 0

    def __init__(self) -> None:
        self.terminated = False

    def poll(self) -> int | None:
        return 0

    def terminate(self, grace_period_s: float | None = None, **_kwargs: object) -> None:
        del grace_period_s
        self.terminated = True


def _watchdog(clock: FakeClock) -> IdleWatchdog:
    watchdog = IdleWatchdog(TimeoutPolicy(idle_timeout_seconds=300.0), clock)
    watchdog.record_invocation_start()
    return watchdog


def test_an_exit_observed_this_instant_is_not_yet_evidence() -> None:
    clock = FakeClock(start=0.0)
    watchdog = _watchdog(clock)
    clock.advance(0.1)
    handle = _ExitedHandle()

    check_broken_agent_timer(handle, watchdog, "claude")

    assert handle.terminated is False


def test_a_settled_exit_with_no_output_is_still_broken() -> None:
    clock = FakeClock(start=0.0)
    watchdog = _watchdog(clock)
    clock.advance(0.1)
    handle = _ExitedHandle()
    check_broken_agent_timer(handle, watchdog, "claude")
    clock.advance(BROKEN_AGENT_EXIT_SETTLE_SECONDS)

    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_broken_agent_timer(handle, watchdog, "claude")

    assert excinfo.value.reason == "no_output"


def test_output_arriving_after_the_exit_clears_the_diagnosis() -> None:
    clock = FakeClock(start=0.0)
    watchdog = _watchdog(clock)
    clock.advance(0.1)
    handle = _ExitedHandle()
    check_broken_agent_timer(handle, watchdog, "claude")

    watchdog.record_tool_use_activity()
    clock.advance(BROKEN_AGENT_EXIT_SETTLE_SECONDS)
    check_broken_agent_timer(handle, watchdog, "claude")

    clock.advance(BROKEN_AGENT_OUTPUT_GRACE_SECONDS)
    check_broken_agent_timer(handle, watchdog, "claude")
    assert handle.terminated is False


def test_the_settle_window_stays_inside_both_kill_windows() -> None:
    assert 0.0 < BROKEN_AGENT_EXIT_SETTLE_SECONDS < BROKEN_AGENT_OUTPUT_GRACE_SECONDS
    assert BROKEN_AGENT_EXIT_SETTLE_SECONDS < NO_OUTPUT_AT_START_SECONDS


def test_a_widened_drain_window_widens_the_settle_window() -> None:
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        TimeoutPolicy(idle_timeout_seconds=300.0, drain_window_seconds=4.0), clock
    )
    watchdog.record_invocation_start()
    clock.advance(0.1)
    handle = _ExitedHandle()

    check_broken_agent_timer(handle, watchdog, "claude")
    clock.advance(BROKEN_AGENT_EXIT_SETTLE_SECONDS)
    check_broken_agent_timer(handle, watchdog, "claude")
    assert handle.terminated is False

    clock.advance(4.0)
    with pytest.raises(BrokenAgentExitError):
        check_broken_agent_timer(handle, watchdog, "claude")


def test_a_narrowed_drain_window_cannot_remove_the_settle_window() -> None:
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        TimeoutPolicy(idle_timeout_seconds=300.0, drain_window_seconds=0.0), clock
    )
    watchdog.record_invocation_start()
    clock.advance(0.1)
    handle = _ExitedHandle()

    check_broken_agent_timer(handle, watchdog, "claude")

    assert handle.terminated is False


def test_a_failed_liveness_probe_does_not_rewind_the_window() -> None:
    clock = FakeClock(start=0.0)
    watchdog = _watchdog(clock)
    clock.advance(0.1)
    watchdog.record_process_liveness(is_alive=False)
    clock.advance(BROKEN_AGENT_EXIT_SETTLE_SECONDS)

    watchdog.record_process_liveness(is_alive=True, observed=False)

    settled_for = watchdog.seconds_since_process_exit
    assert settled_for is not None
    assert settled_for >= BROKEN_AGENT_EXIT_SETTLE_SECONDS


def test_a_process_observed_alive_again_restarts_the_window() -> None:
    clock = FakeClock(start=0.0)
    watchdog = _watchdog(clock)
    clock.advance(0.1)
    watchdog.record_process_liveness(is_alive=False)
    assert watchdog.seconds_since_process_exit == 0.0

    watchdog.record_process_liveness(is_alive=True)

    assert watchdog.seconds_since_process_exit is None


def test_a_process_never_seen_dead_reports_no_exit() -> None:
    clock = FakeClock(start=0.0)
    watchdog = _watchdog(clock)
    clock.advance(30.0)

    assert watchdog.seconds_since_process_exit is None


def test_a_reused_watchdog_does_not_inherit_the_previous_exit() -> None:
    clock = FakeClock(start=0.0)
    watchdog = _watchdog(clock)
    clock.advance(0.1)
    watchdog.record_process_liveness(is_alive=False)
    clock.advance(30.0)

    watchdog.record_invocation_start()

    assert watchdog.seconds_since_process_exit is None
    clock.advance(0.1)
    handle = _ExitedHandle()
    check_broken_agent_timer(handle, watchdog, "claude")
    assert handle.terminated is False
