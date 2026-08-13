"""Regression coverage for clean quick exits with no LLM evidence (S-6)."""

from __future__ import annotations

import pytest

from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy
from ralph.agents.invoke import BrokenAgentExitError, check_broken_agent_timer
from ralph.agents.timeout_clock import FakeClock


class _ManagedHandle:
    pid = None

    def __init__(self, returncode: int | None) -> None:
        self._returncode = returncode
        self.terminated = False

    def poll(self) -> int | None:
        return self._returncode

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s
        self.terminated = True


def _watchdog(clock: FakeClock) -> IdleWatchdog:
    watchdog = IdleWatchdog(TimeoutPolicy(idle_timeout_seconds=30.0), clock)
    watchdog.record_invocation_start()
    return watchdog


def test_quick_clean_exit_without_evidence_falls_over_before_grace_window() -> None:
    """S-6: an exited process with no output or session proof fails on the next poll."""
    clock = FakeClock(start=0.0)
    handle = _ManagedHandle(returncode=0)
    watchdog = _watchdog(clock)
    clock.advance(2.0)

    with pytest.raises(BrokenAgentExitError, match="no meaningful LLM output") as excinfo:
        check_broken_agent_timer(handle, watchdog, "opencode")

    assert excinfo.value.reason == "no_output"
    assert excinfo.value.elapsed_seconds == 2.0


def test_live_silent_process_keeps_startup_grace_window() -> None:
    """S-6: a live slow-starting process is not killed at five seconds."""
    clock = FakeClock(start=0.0)
    handle = _ManagedHandle(returncode=None)
    watchdog = _watchdog(clock)
    clock.advance(5.0)

    check_broken_agent_timer(handle, watchdog, "opencode")

    assert handle.terminated is False


def test_meaningful_output_prevents_early_exit_classification() -> None:
    """S-6: a completed agent that emitted genuine LLM activity is not a no-output failure."""
    clock = FakeClock(start=0.0)
    handle = _ManagedHandle(returncode=0)
    watchdog = _watchdog(clock)
    watchdog.record_tool_use_activity()
    clock.advance(2.0)

    check_broken_agent_timer(handle, watchdog, "opencode")

    assert handle.terminated is False
