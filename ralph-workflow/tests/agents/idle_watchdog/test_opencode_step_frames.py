"""OpenCode step frames must not count as forward progress.

OpenCode brackets EVERY tool call with ``step_start`` / ``step_finish``. They
classified as OUTPUT_LINE, which routes to ``record_activity()`` ->
``RepetitionTracker.note_progress()``, so both repetition streaks were reset
after every single call and neither REPEATED_IDENTICAL_TOOL_CALL nor
REPEATED_ERROR_LOOP could ever accumulate on the real interleaved stream.
The parser yields no output line for these frames, so they are cosmetic.
"""

from __future__ import annotations

import json
from types import MethodType, SimpleNamespace

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import AgentExecutionState, strategy_for_transport
from ralph.agents.idle_watchdog import IdleWatchdog, WatchdogFireReason, WatchdogVerdict
from ralph.agents.idle_watchdog.timeout_policy import TimeoutPolicy
from ralph.agents.invoke._process_reader import _ProcessLineReader
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport


def _frame(event_type: str) -> str:
    return json.dumps(
        {
            "type": event_type,
            "timestamp": 1785133506972,
            "sessionID": "ses_1",
            "part": {"id": "prt_1", "type": event_type.replace("_", "-")},
        }
    )


def _tool_line(command: str) -> str:
    return json.dumps(
        {
            "type": "tool_use",
            "timestamp": 1785133508187,
            "sessionID": "ses_1",
            "part": {
                "type": "tool",
                "tool": "ralph_exec",
                "callID": "call_1",
                "state": {"status": "completed", "input": {"command": command}, "output": "x"},
            },
        }
    )


def test_step_frames_classify_as_lifecycle() -> None:
    strategy = strategy_for_transport(AgentTransport.OPENCODE)

    assert strategy.classify_activity_line(_frame("step_start")) is not None
    assert strategy.classify_activity_line(_frame("step_start")).kind == (
        AgentActivityKind.LIFECYCLE
    )
    assert strategy.classify_activity_line(_frame("step_finish")).kind == (
        AgentActivityKind.LIFECYCLE
    )


def test_wedge_trips_on_the_real_interleaved_stream() -> None:
    """The real stream brackets each call with frames; the wedge must survive it.

    This is the end-to-end claim: replay exactly what OpenCode emits, through
    the real strategy and the real line reader, and the breaker must still fire.
    """
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=5,
            repeated_error_window_count=8,
            repeated_error_window_seconds=600.0,
            activity_evidence_ttl_seconds=None,
            post_tool_result_progression_seconds=None,
        ),
        clock,
    )
    reader = SimpleNamespace(
        _strategy=strategy_for_transport(AgentTransport.OPENCODE),
        _last_activity_kind="",
        _last_activity_meaningful=[False],
    )
    record = MethodType(_ProcessLineReader._record_line_activity, reader)

    for _ in range(5):
        record(watchdog, _frame("step_start") + "\n")
        record(watchdog, _tool_line("uv run pytest -q") + "\n")
        record(watchdog, _frame("step_finish") + "\n")
        clock.advance(2.0)

    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) == (
        WatchdogVerdict.FIRE
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL


def test_distinct_calls_on_the_real_stream_do_not_trip() -> None:
    """The same replay with DIFFERENT commands must stay quiet."""
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=5,
            repeated_error_window_count=8,
            repeated_error_window_seconds=600.0,
            activity_evidence_ttl_seconds=None,
            post_tool_result_progression_seconds=None,
        ),
        clock,
    )
    reader = SimpleNamespace(
        _strategy=strategy_for_transport(AgentTransport.OPENCODE),
        _last_activity_kind="",
        _last_activity_meaningful=[False],
    )
    record = MethodType(_ProcessLineReader._record_line_activity, reader)

    for index in range(10):
        record(watchdog, _frame("step_start") + "\n")
        record(watchdog, _tool_line(f"echo {index}") + "\n")
        record(watchdog, _frame("step_finish") + "\n")
        clock.advance(2.0)

    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) != (
        WatchdogVerdict.FIRE
    )
