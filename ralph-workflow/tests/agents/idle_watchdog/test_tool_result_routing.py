"""A TOOL_RESULT must reach ``record_tool_result_activity`` on EVERY transport.

``_ProcessLineReader`` serves every non-PTY transport (PTY is only
claude_interactive / agy / nanocoder) and had no TOOL_RESULT branch, so a tool
result fell through to ``record_activity()``. Two consequences:

* ``record_activity`` calls ``RepetitionTracker.note_progress()``, which wiped
  the tool-call streak after every completed call -- putting back exactly what
  ``record_tool_result_activity`` documents it must not do.
* ``record_tool_result_activity`` was never called, so
  ``STALLED_AFTER_TOOL_RESULT`` was unreachable on opencode, claude, pi,
  cursor, and codex alike.
"""

from __future__ import annotations

import json
from types import MethodType, SimpleNamespace

from ralph.agents.activity import AgentActivityKind, AgentActivitySignal
from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import IdleWatchdog, WatchdogFireReason, WatchdogVerdict
from ralph.agents.idle_watchdog.timeout_policy import TimeoutPolicy
from ralph.agents.invoke._process_reader import _ProcessLineReader
from ralph.agents.timeout_clock import FakeClock


class _ResultThenCallStrategy:
    """Emits TOOL_USE for tool_use lines and TOOL_RESULT for result lines."""

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        payload = json.loads(line)
        kind = (
            AgentActivityKind.TOOL_RESULT
            if payload.get("type") == "tool_result"
            else AgentActivityKind.TOOL_USE
        )
        return AgentActivitySignal(kind, raw=line)

    def observe_line(self, line: str) -> None:
        del line


def _reader() -> object:
    return SimpleNamespace(
        _strategy=_ResultThenCallStrategy(),
        _last_activity_kind="",
        _last_activity_meaningful=[False],
    )


def _watchdog(clock: FakeClock) -> IdleWatchdog:
    return IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=5,
            repeated_error_window_count=None,
            repeated_error_window_seconds=None,
            activity_evidence_ttl_seconds=None,
            post_tool_result_progression_seconds=None,
        ),
        clock,
    )


def test_tool_result_does_not_wipe_the_tool_call_streak() -> None:
    """Five identical calls must trip even though each result arrives."""
    clock = FakeClock()
    watchdog = _watchdog(clock)
    record = MethodType(_ProcessLineReader._record_line_activity, _reader())
    call = json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "ls"}})
    result = json.dumps({"type": "tool_result", "output": "ok"})

    for _ in range(5):
        record(watchdog, call + "\n")
        record(watchdog, result + "\n")
        clock.advance(1.0)

    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) == (
        WatchdogVerdict.FIRE
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL


def test_tool_result_is_attributed_to_the_post_tool_result_stall() -> None:
    """The wedge must be reported as STALLED_AFTER_TOOL_RESULT, not a generic
    NO_OUTPUT_DEADLINE, which is all an off-PTY transport could produce before.
    """
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            post_tool_result_progression_seconds=120.0,
            repeated_error_consecutive_threshold=None,
            repeated_error_window_count=None,
            repeated_error_window_seconds=None,
            activity_evidence_ttl_seconds=None,
        ),
        clock,
    )
    record = MethodType(_ProcessLineReader._record_line_activity, _reader())

    record(watchdog, json.dumps({"type": "tool_result", "output": "ok"}) + "\n")
    clock.advance(400.0)

    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) == (
        WatchdogVerdict.FIRE
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.STALLED_AFTER_TOOL_RESULT


def test_tool_result_counts_as_meaningful_output() -> None:
    """A tool result IS real output, so NO_OUTPUT_AT_START must not fire."""
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=30.0,
            post_tool_result_progression_seconds=None,
            repeated_error_consecutive_threshold=None,
            repeated_error_window_count=None,
            repeated_error_window_seconds=None,
            activity_evidence_ttl_seconds=None,
        ),
        clock,
    )
    record = MethodType(_ProcessLineReader._record_line_activity, _reader())

    record(watchdog, json.dumps({"type": "tool_result", "output": "ok"}) + "\n")
    clock.advance(60.0)

    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert watchdog.last_fire_reason != WatchdogFireReason.NO_OUTPUT_AT_START
    assert verdict != WatchdogVerdict.FIRE
