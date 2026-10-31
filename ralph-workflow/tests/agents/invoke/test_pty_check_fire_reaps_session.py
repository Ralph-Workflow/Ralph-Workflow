"""A PTY watchdog fire must sweep the agent's whole session, not just its leader.

``PtyLineReader._check_fire`` terminated the handle and then ran
``teardown_subtree`` only ``if self._handle.poll() is None``. ``terminate``
waits for and reaps the leader, so ``poll()`` is never ``None`` at that point
and the sweep was skipped on every idle / session-ceiling fire. Session members
that had re-parented away from the leader (MCP servers, backgrounded tools)
survived every fire. Every other PTY fire path tears down unconditionally.

The handle double below behaves like the real one: ``terminate`` leaves it
reaped, so ``poll()`` reports an exit code afterwards.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import TYPE_CHECKING

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy, WatchdogVerdict
from ralph.agents.invoke import PtyLineReader
from ralph.agents.timeout_clock import FakeClock

if TYPE_CHECKING:
    import pytest

_LEADER_PID = 4343
_SIGTERM_RC = -15


class _ReapingHandle:
    """PTY handle double whose ``terminate`` reaps the leader, as the real one does."""

    def __init__(self) -> None:
        self.pid = _LEADER_PID
        self.master_fd = 7
        self.returncode: int | None = None
        self.reasons: list[str] = []

    def record_terminal_reason(self, reason: str) -> None:
        self.reasons.append(reason)

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s
        self.returncode = _SIGTERM_RC

    def poll(self) -> int | None:
        return self.returncode

    def descendant_snapshot(self) -> tuple[int, float | None]:
        return (0, None)


def _reader(handle: _ReapingHandle, policy: TimeoutPolicy, clock: FakeClock) -> PtyLineReader:
    reader = object.__new__(PtyLineReader)
    reader._handle = handle
    reader._agent_name = "claude"
    reader._policy = policy
    reader._clock = clock
    reader._lines_queue = []
    reader._lines_lock = threading.Lock()
    reader._last_hard_stop = [None]
    reader._last_activity_kind = None
    reader._awaiting_post_tool_result_progress = False
    reader._last_tool_result_at = None
    reader._last_tool_result_excerpt = None
    reader._last_tool_use_name = None
    reader._captured_session_id = None
    reader._transcript_session_ids = deque(maxlen=4)
    reader._transcript_session_ids_lock = threading.Lock()
    return reader


def test_watchdog_fire_tears_down_the_session_after_terminate_reaped_the_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    teardowns: list[tuple[int, str]] = []

    def _record_teardown(pid: int, *, issuer: str) -> None:
        teardowns.append((pid, issuer))

    monkeypatch.setattr("ralph.agents.invoke._pty_line_reader.teardown_subtree", _record_teardown)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=10.0,
        no_progress_quiet_seconds=None,
    )
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(policy, clock)
    watchdog.record_invocation_start()
    clock.advance(11.0)
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    handle = _ReapingHandle()

    fired = _reader(handle, policy, clock)._check_fire(watchdog, verdict)

    assert fired is not None
    assert handle.poll() == _SIGTERM_RC, "the double must model a reaped leader"
    assert teardowns == [(_LEADER_PID, "invoke:pty")], (
        "the fire path must sweep the session even though terminate() reaped the leader"
    )
