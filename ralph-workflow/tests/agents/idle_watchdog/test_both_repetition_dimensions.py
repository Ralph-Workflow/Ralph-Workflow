"""A failed tool call must feed BOTH repetition dimensions.

It is genuinely both things at once, and each dimension catches a wedge the
other cannot:

* the TOOL dimension collapses a repeated call whose failure TEXT varies -- a
  failing test run, where the pytest counts differ every attempt;
* the ERROR dimension collapses a repeated failure whose ARGS vary -- an
  ``MCP error -32001: Request timed out`` storm across different files, which
  is the incident the repeated-error breaker was written for.

Forcing the classifier to pick one kind meant whichever it picked, the other
wedge stayed invisible.
"""

from __future__ import annotations

import json
from types import MethodType, SimpleNamespace

from ralph.agents.execution_state import AgentExecutionState, strategy_for_transport
from ralph.agents.idle_watchdog import IdleWatchdog, WatchdogFireReason, WatchdogVerdict
from ralph.agents.idle_watchdog.timeout_policy import TimeoutPolicy
from ralph.agents.invoke._process_reader import _ProcessLineReader
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport


def _errored_tool_line(path: str, *, call_id: str, error: str) -> str:
    return json.dumps(
        {
            "type": "tool_use",
            "timestamp": 1785133508187,
            "sessionID": "ses_1",
            "part": {
                "type": "tool",
                "tool": "ralph_read_file",
                "callID": call_id,
                "state": {"status": "error", "input": {"path": path}, "error": error},
            },
        }
    )


def _harness() -> tuple[FakeClock, IdleWatchdog, object]:
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
    return clock, watchdog, MethodType(_ProcessLineReader._record_line_activity, reader)


def test_mcp_timeout_storm_with_varying_args_trips_the_error_dimension() -> None:
    """The originating incident: identical error text, DIFFERENT arguments.

    The tool dimension cannot collapse these -- every call has a different
    path -- so only the error dimension can see the storm.
    """
    clock, watchdog, record = _harness()

    for index in range(8):
        record(
            watchdog,
            _errored_tool_line(
                f"/repo/file_{index}.py",
                call_id=f"call_{index}",
                error="MCP error -32001: Request timed out",
            )
            + "\n",
        )
        clock.advance(30.0)

    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) == (
        WatchdogVerdict.FIRE
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_ERROR_LOOP


def test_repeated_failing_command_with_varying_text_trips_the_tool_dimension() -> None:
    """The mirror case: identical arguments, DIFFERENT error text every attempt.

    The error dimension cannot collapse these, so only the tool dimension can
    see the wedge. Both directions must work, which is why the signal feeds
    both rather than choosing one.
    """
    clock, watchdog, record = _harness()

    for index in range(5):
        record(
            watchdog,
            _errored_tool_line(
                "/repo/same.py",
                call_id=f"call_{index}",
                error=f"2 failed, 118 passed in {index}.42s",
            )
            + "\n",
        )
        clock.advance(2.0)

    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) == (
        WatchdogVerdict.FIRE
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL


def test_successful_calls_feed_no_error_dimension() -> None:
    """A completed tool must not contribute anything to the error breaker."""
    clock, watchdog, record = _harness()
    line = json.dumps(
        {
            "type": "tool_use",
            "sessionID": "ses_1",
            "part": {
                "type": "tool",
                "tool": "ralph_read_file",
                "callID": "call_1",
                "state": {"status": "completed", "input": {"path": "/a.py"}, "output": "ok"},
            },
        }
    )

    for _ in range(8):
        record(watchdog, line + "\n")
        clock.advance(30.0)

    assert watchdog.repetition_diagnostic().get("error_fingerprint") is None
