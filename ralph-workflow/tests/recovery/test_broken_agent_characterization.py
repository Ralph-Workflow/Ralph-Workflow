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
    """The floor stays below the startup grace so broken-agent wins the race.

    This used to also pin ``NO_OUTPUT_AT_START_SECONDS == 15.0``. That number
    killed every OpenCode run: its JSON stream emits nothing until the first
    tool call, measured at over 180s on a real prompt. The ORDERING is the
    contract worth pinning here; the value itself is pinned against the
    measurement in ``test_startup_grace_accommodates_silent_agents.py``.
    """
    assert BROKEN_AGENT_OUTPUT_GRACE_SECONDS == 12.0
    assert BROKEN_AGENT_OUTPUT_GRACE_SECONDS < NO_OUTPUT_AT_START_SECONDS


def test_broken_agent_regression_silent_live_process_fails_before_startup_watchdog() -> None:
    """S-12: the reader's live timer falls over a silent agent past its grace.

    The advance tracks the configured startup grace rather than a literal
    35s: the broken-agent deadline is derived from it as
    ``max(12, configured - 3)``, so hardcoding a duration here silently
    stopped testing anything the moment that grace was raised.
    """
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
    # Bounded, not fast: the kill must still land shortly after the configured
    # grace rather than waiting for the cumulative no-progress ceiling. Derived
    # from the grace so raising it cannot silently turn this into a no-op.
    assert clock.monotonic() <= NO_OUTPUT_AT_START_SECONDS + 60.0


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
    """With the default startup grace, the kill defers to it, not to the 12s floor.

    The floor is a FLOOR: ``max(12, configured - 3)``. While the default was 15s
    the derived value was the floor, so this test could not tell the two apart.
    With a default that accommodates a genuinely silent startup, deferring is the
    whole point -- firing at 12s here is what killed OpenCode.
    """
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
