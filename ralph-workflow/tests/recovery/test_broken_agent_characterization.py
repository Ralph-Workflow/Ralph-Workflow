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
    assert BROKEN_AGENT_OUTPUT_GRACE_SECONDS == 30.0
    assert BROKEN_AGENT_OUTPUT_GRACE_SECONDS < NO_OUTPUT_AT_START_SECONDS
    assert NO_OUTPUT_AT_START_SECONDS == 120.0


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
