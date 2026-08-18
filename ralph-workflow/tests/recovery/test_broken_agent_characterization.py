"""Characterization of the fast broken-agent timeout contract."""

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

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s
        self.terminated = True


def _watchdog(clock: FakeClock) -> IdleWatchdog:
    watchdog = IdleWatchdog(TimeoutPolicy(idle_timeout_seconds=300.0), clock)
    watchdog.record_invocation_start()
    return watchdog


def test_broken_agent_grace_is_fast_and_precedes_startup_watchdog() -> None:
    assert BROKEN_AGENT_OUTPUT_GRACE_SECONDS == 12.0
    assert BROKEN_AGENT_OUTPUT_GRACE_SECONDS < NO_OUTPUT_AT_START_SECONDS
    assert NO_OUTPUT_AT_START_SECONDS == 15.0


def test_broken_agent_regression_silent_live_process_fails_before_startup_watchdog() -> None:
    """S-12: the reader's live timer falls over a silent agent at 30 seconds."""
    clock = FakeClock(start=0.0)
    handle = _LiveHandle()
    watchdog = _watchdog(clock)
    clock.advance(35.0)

    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_broken_agent_timer(handle, watchdog, "claude")

    assert handle.terminated is True
    assert excinfo.value.reason == "no_output"
    assert excinfo.value.elapsed_seconds is not None
    assert excinfo.value.elapsed_seconds >= BROKEN_AGENT_OUTPUT_GRACE_SECONDS
    assert clock.monotonic() <= 60.0


def test_broken_agent_regression_live_timer_waits_during_grace_period() -> None:
    """S-12: a silent process remains eligible for output before the 30-second deadline."""
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
    """A configured no_output_at_start_seconds larger than 12s delays the kill."""
    clock = FakeClock(start=0.0)
    handle = _LiveHandle()
    watchdog = _watchdog_with_startup_grace(clock, 120.0)

    # Well past the historical 12s floor, but still inside the extended grace.
    clock.advance(35.0)
    check_broken_agent_timer(handle, watchdog, "claude")
    assert handle.terminated is False

    # Just past the effective grace (120 - 3 = 117s) should fire.
    clock.advance(117.0 - 35.0 + 0.1)
    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_broken_agent_timer(handle, watchdog, "claude")

    assert handle.terminated is True
    assert excinfo.value.reason == "no_output"
    assert excinfo.value.elapsed_seconds is not None
    assert excinfo.value.elapsed_seconds >= 117.0
    assert excinfo.value.grace_seconds == pytest.approx(117.0)


def test_broken_agent_default_startup_grace_still_fires_at_floor() -> None:
    """When no_output_at_start_seconds is the default, the 12s floor still fires."""
    clock = FakeClock(start=0.0)
    handle = _LiveHandle()
    watchdog = _watchdog_with_startup_grace(clock, NO_OUTPUT_AT_START_SECONDS)
    clock.advance(BROKEN_AGENT_OUTPUT_GRACE_SECONDS + 0.1)

    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_broken_agent_timer(handle, watchdog, "claude")

    assert handle.terminated is True
    assert excinfo.value.reason == "no_output"
    assert excinfo.value.grace_seconds == BROKEN_AGENT_OUTPUT_GRACE_SECONDS
