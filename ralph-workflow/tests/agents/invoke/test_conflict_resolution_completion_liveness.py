"""Completion-path liveness regressions for conflict resolution (S-2)."""

from __future__ import annotations

import threading
from pathlib import Path

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy, WatchdogVerdict
from ralph.agents.idle_watchdog.timeout_policy import TimeoutProfile
from ralph.agents.timeout_clock import FakeClock


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
