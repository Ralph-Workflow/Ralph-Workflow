"""An exited process's output is still in flight for a moment.

The fast broken-agent path reads two facts at one instant -- the process
is dead, and no meaningful output has been RECORDED -- and treats the
pair as proof the agent produced nothing. They are not true at the same
instant. A line the agent wrote is recorded only after the reader thread
has been scheduled, pushed it onto the queue and had it classified, so
on a loaded host a healthy agent that writes its output and exits
promptly loses the race and the run fails over with "no meaningful LLM
output; check credentials or provider availability" against an agent
that did its job.

Observed: the sharded verification run failed
``test_invoke_wires_process_monitor_for_transport[claude]``, whose agent
is ``echo``. Nothing in the suite covered the TIMING of the fast path,
so the guard could be reverted with every recovery test green.
"""

from __future__ import annotations

import pytest

from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy
from ralph.agents.invoke import BrokenAgentExitError, check_broken_agent_timer
from ralph.agents.timeout_clock import FakeClock
from ralph.process.manager import ManagedProcess
from ralph.timeout_defaults import (
    BROKEN_AGENT_EXIT_SETTLE_SECONDS,
    BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
)


class _ExitedHandle(ManagedProcess):
    """A process that has already exited cleanly."""

    @property
    def pid(self) -> int:
        return 0

    def __init__(self) -> None:
        self.terminated = False

    def poll(self) -> int | None:
        return 0

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s
        self.terminated = True


def _watchdog(clock: FakeClock) -> IdleWatchdog:
    watchdog = IdleWatchdog(TimeoutPolicy(idle_timeout_seconds=300.0), clock)
    watchdog.record_invocation_start()
    return watchdog


def test_an_exit_observed_this_instant_is_not_yet_evidence() -> None:
    """The first sighting of a dead process does not condemn it."""
    clock = FakeClock(start=0.0)
    watchdog = _watchdog(clock)
    clock.advance(0.1)
    handle = _ExitedHandle()

    check_broken_agent_timer(handle, watchdog, "claude")

    assert handle.terminated is False


def test_a_settled_exit_with_no_output_is_still_broken() -> None:
    """The window defers the verdict; it does not remove it."""
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
    """The incident itself: one line, written late, read after the exit.

    The line lands between the two polls -- which is exactly what a
    starved reader thread does -- and the agent must not be called
    broken for it, then or ever.
    """
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


def test_the_settle_window_stays_inside_the_broken_agent_grace() -> None:
    """A deferral longer than the grace it defers would disable the kill."""
    assert 0.0 < BROKEN_AGENT_EXIT_SETTLE_SECONDS < BROKEN_AGENT_OUTPUT_GRACE_SECONDS
