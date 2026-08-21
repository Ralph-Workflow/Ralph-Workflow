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
    NO_OUTPUT_AT_START_SECONDS,
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


def test_the_settle_window_stays_inside_both_kill_windows() -> None:
    """A deferral longer than the guards it defers would disable them."""
    assert 0.0 < BROKEN_AGENT_EXIT_SETTLE_SECONDS < BROKEN_AGENT_OUTPUT_GRACE_SECONDS
    assert BROKEN_AGENT_EXIT_SETTLE_SECONDS < NO_OUTPUT_AT_START_SECONDS


def test_a_widened_drain_window_widens_the_settle_window() -> None:
    """An operator who says output arrives late is answering THIS question.

    ``drain_window_seconds`` is the knob for "this host needs longer to
    deliver what a process already wrote". Read as a bare constant, the
    settle window ignored the one setting that describes the hazard it
    guards, and a host slow enough to need the fix could not ask for
    more of it.
    """
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
    """Zero drain does not mean zero settle -- the race is not configurable away."""
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
    """An unavailable probe is not evidence the process came back.

    The caller still reports the SAFE liveness so a broken probe cannot
    manufacture a clean-exit diagnosis. Recorded as an observation, that
    safe value discarded the whole accumulated window on every
    intermittent failure -- so a flapping probe starved the verdict for
    the full grace period instead of costing one tick, as it did before
    the window existed.
    """
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
    """A real liveness reading is evidence, and it replaces the stamp.

    Reachable on a watchdog handed a fresh handle. The branch existed
    from the start with nothing exercising it.
    """
    clock = FakeClock(start=0.0)
    watchdog = _watchdog(clock)
    clock.advance(0.1)
    watchdog.record_process_liveness(is_alive=False)
    assert watchdog.seconds_since_process_exit == 0.0

    watchdog.record_process_liveness(is_alive=True)

    assert watchdog.seconds_since_process_exit is None


def test_a_process_never_seen_dead_reports_no_exit() -> None:
    """``None`` means "not observed dead", never "settled long ago".

    The distinction is the whole contract: a caller that reads ``None``
    as a settled exit condemns every agent it has not yet polled.
    """
    clock = FakeClock(start=0.0)
    watchdog = _watchdog(clock)
    clock.advance(30.0)

    assert watchdog.seconds_since_process_exit is None


def test_a_reused_watchdog_does_not_inherit_the_previous_exit() -> None:
    """The previous run's exit is not this run's evidence.

    A stale stamp reads as an exit that settled long ago, so the first
    poll of the NEW invocation condemns the agent outright -- the race
    the window exists to prevent, restored in full and silently.
    """
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
