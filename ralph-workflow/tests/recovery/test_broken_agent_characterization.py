from __future__ import annotations

import pytest

from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy
from ralph.agents.invoke import BrokenAgentExitError, check_broken_agent_timer
from ralph.agents.timeout_clock import FakeClock
from ralph.process.manager import ManagedProcess
from ralph.timeout_defaults import (
    BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
    NO_OUTPUT_AT_START_SECONDS,
)


class _LiveHandle(ManagedProcess):
    @property
    def pid(self) -> int:
        return 0

    def __init__(self) -> None:
        self.terminated = False

    def terminate(self, grace_period_s: float | None = None, **_kwargs: object) -> None:
        del grace_period_s
        self.terminated = True


def _watchdog(clock: FakeClock) -> IdleWatchdog:
    watchdog = IdleWatchdog(TimeoutPolicy(idle_timeout_seconds=300.0), clock)
    watchdog.record_invocation_start()
    return watchdog


def test_broken_agent_grace_is_fast_and_precedes_startup_watchdog() -> None:
    assert BROKEN_AGENT_OUTPUT_GRACE_SECONDS == 12.0
    assert BROKEN_AGENT_OUTPUT_GRACE_SECONDS < NO_OUTPUT_AT_START_SECONDS


def test_broken_agent_regression_silent_live_process_fails_before_startup_watchdog() -> None:
    clock = FakeClock(start=0.0)
    handle = _LiveHandle()
    watchdog = _watchdog(clock)
    clock.advance(NO_OUTPUT_AT_START_SECONDS + 5.0)

    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_broken_agent_timer(handle, watchdog, "claude")

    assert handle.terminated is True
    assert excinfo.value.reason == "no_output"
    assert excinfo.value.elapsed_seconds is not None
    assert excinfo.value.elapsed_seconds >= BROKEN_AGENT_OUTPUT_GRACE_SECONDS
    assert clock.monotonic() <= NO_OUTPUT_AT_START_SECONDS + 60.0


def test_broken_agent_regression_live_timer_waits_during_grace_period() -> None:
    clock = FakeClock(start=0.0)
    handle = _LiveHandle()
    watchdog = _watchdog(clock)
    clock.advance(10.0)

    check_broken_agent_timer(handle, watchdog, "claude")

    assert handle.terminated is False


def _watchdog_with_startup_grace(clock: FakeClock, grace_seconds: float) -> IdleWatchdog:
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=grace_seconds,
        ),
        clock,
    )
    watchdog.record_invocation_start()
    return watchdog


def test_broken_agent_honors_extended_startup_grace() -> None:
    clock = FakeClock(start=0.0)
    handle = _LiveHandle()
    watchdog = _watchdog_with_startup_grace(clock, 120.0)

    clock.advance(35.0)
    check_broken_agent_timer(handle, watchdog, "claude")
    assert handle.terminated is False

    clock.advance(117.0 - 35.0 + 0.1)
    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_broken_agent_timer(handle, watchdog, "claude")

    assert handle.terminated is True
    assert excinfo.value.reason == "no_output"
    assert excinfo.value.elapsed_seconds is not None
    assert excinfo.value.elapsed_seconds >= 117.0
    assert excinfo.value.grace_seconds == pytest.approx(117.0)


def test_broken_agent_default_startup_grace_still_fires_at_floor() -> None:
    clock = FakeClock(start=0.0)
    handle = _LiveHandle()
    watchdog = _watchdog_with_startup_grace(clock, NO_OUTPUT_AT_START_SECONDS)
    expected_grace = max(BROKEN_AGENT_OUTPUT_GRACE_SECONDS, NO_OUTPUT_AT_START_SECONDS - 3.0)
    clock.advance(BROKEN_AGENT_OUTPUT_GRACE_SECONDS + 0.1)

    assert check_broken_agent_timer(handle, watchdog, "claude") is None
    assert handle.terminated is False

    clock.advance(expected_grace)

    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_broken_agent_timer(handle, watchdog, "claude")

    assert handle.terminated is True
    assert excinfo.value.reason == "no_output"
    assert excinfo.value.grace_seconds == expected_grace
