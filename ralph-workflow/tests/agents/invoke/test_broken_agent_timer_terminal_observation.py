from __future__ import annotations

import pytest

from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy
from ralph.agents.invoke import BrokenAgentExitError, check_broken_agent_timer
from ralph.agents.invoke.attempt_supervision import AttemptSupervisor, CompletionObserved
from ralph.agents.timeout_clock import FakeClock
from ralph.process.manager import ManagedProcess
from ralph.timeout_defaults import (
    BROKEN_AGENT_EXIT_SETTLE_SECONDS,
    BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "a recorded terminal observation suppresses the broken agent diagnosis": (
        "the broken agent detector used to raise BrokenAgentExitError for an attempt whose "
        "supervisor had already recorded a terminal observation, so a completion that Ralph "
        "terminated with SIGTERM was reported as a broken agent with no meaningful output; "
        "the detector now resolves the attempt supervisor and stands down when that attempt "
        "already has a winner"
    ),
}


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


class _LiveHandle(ManagedProcess):
    @property
    def pid(self) -> int:
        return 0

    def __init__(self) -> None:
        self.terminated = False

    def poll(self) -> int | None:
        return None

    def terminate(self, grace_period_s: float | None = None, **_kwargs: object) -> None:
        del grace_period_s
        self.terminated = True


def _watchdog(clock: FakeClock) -> IdleWatchdog:
    watchdog = IdleWatchdog(TimeoutPolicy(idle_timeout_seconds=300.0), clock)
    watchdog.record_invocation_start()
    return watchdog


def _settled_dead_watchdog(clock: FakeClock) -> IdleWatchdog:
    watchdog = _watchdog(clock)
    clock.advance(0.1)
    handle = _ExitedHandle()
    check_broken_agent_timer(handle, watchdog, "claude")
    clock.advance(BROKEN_AGENT_EXIT_SETTLE_SECONDS)
    return watchdog


def _grace_window_watchdog(clock: FakeClock) -> IdleWatchdog:
    watchdog = IdleWatchdog(
        TimeoutPolicy(idle_timeout_seconds=300.0, no_output_at_start_seconds=None),
        clock,
    )
    watchdog.record_invocation_start()
    clock.advance(BROKEN_AGENT_OUTPUT_GRACE_SECONDS + 0.1)
    return watchdog


def test_a_recorded_completion_suppresses_the_broken_agent_diagnosis() -> None:
    clock = FakeClock(start=0.0)
    watchdog = _settled_dead_watchdog(clock)
    handle = _ExitedHandle()
    supervisor = AttemptSupervisor(handle)
    supervisor.act(CompletionObserved())
    winner = supervisor.winner

    check_broken_agent_timer(handle, watchdog, "claude", supervisor=supervisor)

    assert supervisor.winner is winner


def test_an_unresolved_attempt_still_reports_the_broken_agent() -> None:
    clock = FakeClock(start=0.0)
    watchdog = _settled_dead_watchdog(clock)
    handle = _ExitedHandle()
    supervisor = AttemptSupervisor(handle)

    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_broken_agent_timer(handle, watchdog, "claude", supervisor=supervisor)

    assert excinfo.value.reason == "no_output"


def test_a_recorded_completion_on_the_attempt_recorder_suppresses_the_diagnosis() -> None:
    clock = FakeClock(start=0.0)
    watchdog = _settled_dead_watchdog(clock)
    handle = _ExitedHandle()
    supervisor = AttemptSupervisor(handle)
    supervisor.act(CompletionObserved())

    check_broken_agent_timer(handle, watchdog, "claude", termination_recorder=supervisor.recorder)

    assert supervisor.winner is not None


def test_a_recorded_completion_suppresses_the_grace_window_diagnosis() -> None:
    clock = FakeClock(start=0.0)
    watchdog = _grace_window_watchdog(clock)
    handle = _LiveHandle()
    supervisor = AttemptSupervisor(handle)
    supervisor.act(CompletionObserved())
    winner = supervisor.winner

    check_broken_agent_timer(handle, watchdog, "claude", supervisor=supervisor)

    assert supervisor.winner is winner


def test_an_unresolved_attempt_still_reports_the_grace_window_diagnosis() -> None:
    clock = FakeClock(start=0.0)
    watchdog = _grace_window_watchdog(clock)
    handle = _LiveHandle()

    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_broken_agent_timer(handle, watchdog, "claude")

    assert excinfo.value.reason == "no_output"
    assert handle.terminated is True
