"""Completion-path liveness regressions for conflict resolution (S-2)."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy, WatchdogVerdict
from ralph.agents.idle_watchdog.timeout_policy import TimeoutProfile
from ralph.agents.invoke import AgentRunCtx
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig


def test_conflict_resolution_regression_parent_exit_keeps_scoped_activity_supervised() -> None:
    """S-2/R3: parent exit cannot discard fresh scoped MCP activity as idle."""
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=900.0,
            profile=TimeoutProfile.ACTIVITY_ONLY,
            process_exit_wait_seconds=0.0,
            descendant_wait_timeout_seconds=0.0,
        ),
        clock,
    )
    watchdog.record_invocation_start()
    clock.advance(899.0)
    watchdog.record_mcp_tool_call()

    assert watchdog.evaluate(lambda: AgentExecutionState.WAITING_ON_CHILD) is WatchdogVerdict.CONTINUE


class _ParentExitedHandle:
    """Parent already exited while a scoped resolver child remains active."""

    stdout = iter(())
    pid = None

    def poll(self) -> int:
        return 0

    def terminate(self, *, grace_period_s: float = 0.5) -> None:
        del grace_period_s


class _ScopedChildStrategy:
    """Reports active scoped work while the parent is already terminal."""

    def __init__(self) -> None:
        self.active = True

    def classify_quiet(self, _handle: object, _probe: object) -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD if self.active else AgentExecutionState.ACTIVE

    def classify_activity_line(self, _line: str) -> None:
        return None


class _CompletionClock(FakeClock):
    """Advances only injected time and feeds fresh MCP activity to the watchdog."""

    def __init__(self, strategy: _ScopedChildStrategy) -> None:
        super().__init__()
        self.strategy = strategy
        self.watchdog: IdleWatchdog | None = None
        self.waits = 0

    def wait_for_event(self, event: threading.Event, seconds: float) -> bool:
        self.advance(seconds)
        self.waits += 1
        if self.watchdog is not None:
            self.watchdog.record_mcp_tool_call()
        if self.waits == 4:
            self.strategy.active = False
        return event.is_set()


class _AlwaysScopedChildStrategy(_ScopedChildStrategy):
    """Keeps the PTY done path in scoped-work mode until inactivity fires."""

    def classify_quiet(self, _handle: object, _probe: object) -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD


class _PtyCompletionClock(_CompletionClock):
    """Feeds four live MCP events, then drives the one allowed inactivity verdict."""

    def wait_for_event(self, event: threading.Event, seconds: float) -> bool:
        self.waits += 1
        if self.waits <= 4 and self.watchdog is not None:
            self.advance(seconds)
            self.watchdog.record_mcp_tool_call()
            return event.is_set()
        self.advance(10.0)
        return event.is_set()


class _PtyParentExitedHandle:
    """PTY parent already exited while a scoped resolver child remains active."""

    pid = 1

    def __init__(self, master_fd: int) -> None:
        self.master_fd = master_fd

    def poll(self) -> int:
        return 0

    def terminate(self, grace_period_s: float = 0.5) -> None:
        del grace_period_s

    def close(self) -> None:
        return None


def test_conflict_resolution_regression_pty_parent_exit_has_no_elapsed_drain() -> None:
    """S-3/R1: a PTY resolver ends only after scoped MCP liveness becomes silent."""
    read_fd, write_fd = os.pipe()
    reader: PtyLineReader | None = None
    strategy = _AlwaysScopedChildStrategy()
    clock = _PtyCompletionClock(strategy)
    try:
        ctx = AgentRunCtx(
            config=AgentConfig(cmd="resolver", transport=AgentTransport.CLAUDE_INTERACTIVE),
            show_progress=False,
            extra_env=None,
            workspace_path=None,
            policy=TimeoutPolicy(
                idle_timeout_seconds=10.0,
                profile=TimeoutProfile.ACTIVITY_ONLY,
                drain_window_seconds=0.1,
                idle_poll_interval_seconds=0.1,
            ),
            execution_strategy=strategy,
        )
        reader = PtyLineReader(_PtyParentExitedHandle(read_fd), "resolver", ctx, clock, extras=None)
        watchdog = IdleWatchdog(ctx.policy, clock)
        watchdog.record_invocation_start()
        clock.watchdog = watchdog

        with pytest.raises(RuntimeError) as exc_info:
            list(reader._handle_done_path(watchdog))

        assert exc_info.value.reason.value == "conflict_inactivity"
        assert clock.waits == 5
        assert clock.monotonic() == 10.4
    finally:
        os.close(write_fd)
        if reader is not None:
            os.close(reader._input_writer_fd)
            os.close(reader._read_fd)


def test_conflict_resolution_regression_pty_termination_exposes_direct_liveness_metadata() -> None:
    """S-3/R7: a PTY inactivity verdict carries the same direct fields as subprocess mode."""
    read_fd, write_fd = os.pipe()
    reader: PtyLineReader | None = None
    try:
        clock = FakeClock()
        ctx = AgentRunCtx(
            config=AgentConfig(cmd="resolver", transport=AgentTransport.CLAUDE_INTERACTIVE),
            show_progress=False,
            extra_env=None,
            workspace_path=None,
            policy=TimeoutPolicy(idle_timeout_seconds=10.0, profile=TimeoutProfile.ACTIVITY_ONLY),
        )
        reader = PtyLineReader(_PtyParentExitedHandle(read_fd), "resolver", ctx, clock, extras=None)
        watchdog = IdleWatchdog(ctx.policy, clock)
        watchdog.record_invocation_start()
        clock.advance(10.0)

        fire_result = reader._check_fire(watchdog, watchdog.evaluate(lambda: AgentExecutionState.ACTIVE))
        assert fire_result is not None
        _pending, timeout_error = fire_result

        diagnostic = timeout_error.diagnostic
        assert diagnostic["last_activity_kind"] == "stdout"
        assert diagnostic["last_activity_at"] == 0.0
        assert diagnostic["invocation_elapsed_seconds"] == 10.0
    finally:
        os.close(write_fd)
        if reader is not None:
            os.close(reader._input_writer_fd)
            os.close(reader._read_fd)


def test_conflict_resolution_regression_parent_exit_disables_elapsed_reader_drain(
    tmp_path: Path,
) -> None:
    """S-2/DA-001-003: the production done path cannot derive a drain deadline."""
    from ralph.agents.invoke._process_reader import make_line_reader
    from ralph.agents.invoke._types import ProcessReaderCtx
    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig

    strategy = _ScopedChildStrategy()
    clock = _CompletionClock(strategy)
    reader = make_line_reader(
        _ParentExitedHandle(),
        ProcessReaderCtx(
            config=AgentConfig(cmd="resolver", transport=AgentTransport.GENERIC),
            policy=TimeoutPolicy(
                idle_timeout_seconds=10.0,
                profile=TimeoutProfile.ACTIVITY_ONLY,
                drain_window_seconds=0.1,
                idle_poll_interval_seconds=0.1,
            ),
            execution_strategy=strategy,
            workspace_path=tmp_path,
        ),
        clock,
    )
    watchdog = IdleWatchdog(reader._policy, clock)
    watchdog.record_invocation_start()
    clock.watchdog = watchdog

    # Four fresh MCP events carry this completion path well beyond the
    # ordinary 0.1s drain interval.  It may return only after scoped work
    # quiesces, never merely because elapsed drain time expired.
    assert reader._finish_reader_done(watchdog) is None
    assert clock.waits == 4
    assert clock.monotonic() == 0.4
