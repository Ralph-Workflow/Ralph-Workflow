"""Regression coverage for clean quick exits with no LLM evidence (S-6)."""

from __future__ import annotations

from io import StringIO

import pytest

from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy
from ralph.agents.invoke import BrokenAgentExitError, check_broken_agent_timer
from ralph.agents.timeout_clock import FakeClock
from ralph.timeout_defaults import (
    BROKEN_AGENT_EXIT_SETTLE_SECONDS,
    BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
)


class _ManagedHandle:
    pid = None

    def __init__(self, returncode: int | None, *, stderr_text: str = "") -> None:
        self._returncode = returncode
        self.stderr = StringIO(stderr_text)
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
    """S-6: an exited process with no output or session proof fails over fast.

    "On the next poll" was too fast to be true. The exit and the absence
    of output are read at one instant, and a line the agent wrote is
    recorded only once the reader thread has been scheduled -- so the
    next poll after the exit condemned agents whose output was still in
    flight. The verdict now waits one settle window, which is still an
    order of magnitude inside the grace window this test is named for.
    """
    clock = FakeClock(start=0.0)
    handle = _ManagedHandle(returncode=23)
    watchdog = _watchdog(clock)
    clock.advance(2.0)

    check_broken_agent_timer(handle, watchdog, "opencode")
    clock.advance(BROKEN_AGENT_EXIT_SETTLE_SECONDS)

    with pytest.raises(BrokenAgentExitError, match="no meaningful LLM output") as excinfo:
        check_broken_agent_timer(handle, watchdog, "opencode")

    assert excinfo.value.reason == "no_output"
    assert excinfo.value.returncode == 23
    assert "code 23" in str(excinfo.value)
    assert excinfo.value.elapsed_seconds == 2.0 + BROKEN_AGENT_EXIT_SETTLE_SECONDS
    assert excinfo.value.elapsed_seconds < BROKEN_AGENT_OUTPUT_GRACE_SECONDS


def test_quick_exit_regression_retains_stderr_with_real_nonzero_exit_code() -> None:
    """S-2: a settled instant crash preserves bounded stderr diagnostic evidence."""
    clock = FakeClock(start=0.0)
    handle = _ManagedHandle(returncode=37, stderr_text="provider bootstrap rejected credentials")
    watchdog = _watchdog(clock)
    clock.advance(2.0)
    check_broken_agent_timer(handle, watchdog, "pi")
    clock.advance(BROKEN_AGENT_EXIT_SETTLE_SECONDS)

    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_broken_agent_timer(handle, watchdog, "pi")

    assert excinfo.value.returncode == 37
    assert excinfo.value.stderr == "provider bootstrap rejected credentials"
    assert "code 37" in str(excinfo.value)
    assert "provider bootstrap rejected credentials" in str(excinfo.value)


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
