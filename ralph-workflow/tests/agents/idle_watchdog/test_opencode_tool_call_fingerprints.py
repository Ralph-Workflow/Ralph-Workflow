"""OpenCode tool-call fingerprints must be per-tool, not a single blob.

OpenCode carries the tool name and its arguments INSIDE ``part``, never at the
top level::

    {"type": "tool_use", "sessionID": "ses_...",
     "part": {"type": "tool", "tool": "todowrite", "callID": "call_...",
              "state": {"status": "completed", "input": {...}, "output": "..."}}}

Read through the top level -- as ``_resolve_tool_name_and_args`` did before
this -- and EVERY OpenCode tool call resolves to ``("unknown", {})``. Eight
calls to eight DIFFERENT tools then look to
:class:`~ralph.agents.idle_watchdog.repetition_tracker.RepetitionTracker` like
one identical call repeated eight times, and the watchdog kills a healthy
agent with ``REPEATED_IDENTICAL_TOOL_CALL``. Observed live against OpenCode
1.17.15 via ``ralph smoke-interactive-opencode``.

The companion reachability tests live in
``test_mark_tool_call_runtime_reachability.py``; this module owns the OpenCode
wire shape and the two directions that matter: distinct calls must NOT trip,
identical calls MUST still trip.
"""

from __future__ import annotations

import json

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import AgentExecutionState, strategy_for_transport
from ralph.agents.idle_watchdog import IdleWatchdog, WatchdogFireReason, WatchdogVerdict
from ralph.agents.idle_watchdog.timeout_policy import TimeoutPolicy
from ralph.agents.invoke._tool_call_extraction import extract_tool_call_from_activity_signal
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport

# ---------------------------------------------------------------------------
# (6) OpenCode: the part-nested tool envelope must fingerprint per tool.
#
#     OpenCode carries the tool name and its arguments INSIDE ``part``, never
#     at the top level. Read through the top level and EVERY OpenCode tool call
#     resolves to ("unknown", {}), so N consecutive calls to N DIFFERENT tools
#     look like one identical call repeated N times and the watchdog kills a
#     healthy agent with REPEATED_IDENTICAL_TOOL_CALL. Observed live against
#     OpenCode 1.17.15 via ``ralph smoke-interactive-opencode``.
# ---------------------------------------------------------------------------


def _opencode_tool_line(
    tool: str,
    tool_input: dict[str, object],
    *,
    call_id: str = "call_1",
    status: str = "completed",
) -> str:
    """Build a real OpenCode ``tool_use`` line (shape from a live 1.17.15 run)."""
    state: dict[str, object] = {"status": status, "input": tool_input}
    if status == "completed":
        state["output"] = "ok"
    elif status == "error":
        state["error"] = "MCP error -32001: Request timed out"
    return json.dumps(
        {
            "type": "tool_use",
            "timestamp": 1785133508187,
            "sessionID": "ses_05dc0769cffeI7fO3oF7uFd0BQ",
            "part": {
                "type": "tool",
                "tool": tool,
                "callID": call_id,
                "state": state,
            },
        }
    )


def test_extract_tool_call_from_opencode_part_nested_envelope() -> None:
    """OpenCode's ``part.tool`` / ``part.state.input`` MUST be unwrapped."""
    line = _opencode_tool_line("ralph_git_status", {"format": "compact"})

    result = extract_tool_call_from_activity_signal(line)

    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "ralph_git_status"
    assert tool_args == {"format": "compact"}


def test_opencode_distinct_tool_calls_produce_distinct_fingerprints() -> None:
    """Different OpenCode tools MUST NOT collapse onto one fingerprint."""
    first = extract_tool_call_from_activity_signal(
        _opencode_tool_line("ralph_git_status", {"format": "compact"})
    )
    second = extract_tool_call_from_activity_signal(
        _opencode_tool_line("todowrite", {"todos": [{"content": "a"}]}, call_id="call_2")
    )

    assert first != second


def test_extract_tool_call_ignores_opencode_non_tool_part() -> None:
    """A ``step-start`` part carries no tool, so no fingerprint may be produced."""
    line = json.dumps(
        {
            "type": "tool_use",
            "part": {"type": "step-start", "id": "prt_1"},
        }
    )

    assert extract_tool_call_from_activity_signal(line) is None


def test_extract_tool_call_returns_none_when_nothing_distinguishing() -> None:
    """An envelope with neither a name nor args MUST be skipped, not
    fingerprinted as ``("unknown", {})``.

    Collapsing every unreadable envelope onto one fingerprint is what let a
    healthy agent look wedged: the breaker counted unrelated calls as repeats.
    """
    line = json.dumps({"type": "tool_use"})

    assert extract_tool_call_from_activity_signal(line) is None


def test_opencode_strategy_classifies_tool_use_as_tool_use() -> None:
    """The OpenCode strategy must surface tool calls as TOOL_USE."""
    strategy = strategy_for_transport(AgentTransport.OPENCODE)
    line = _opencode_tool_line("ralph_read_file", {"path": "/tmp/x"})

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE
    assert signal.raw == line


def test_opencode_strategy_classifies_errored_tool_as_error_line() -> None:
    """A tool whose ``part.state.status`` is ``error`` is an ERROR_LINE.

    ``_opencode_tool_signal`` runs before ``_error_output_signal``, so without
    this branch the error classifier is unreachable on OpenCode and an MCP
    retry storm is misrouted into the tool-call dimension with a useless
    ``tool_name=unknown`` diagnostic instead of firing REPEATED_ERROR_LOOP
    with the real error text.
    """
    strategy = strategy_for_transport(AgentTransport.OPENCODE)
    line = _opencode_tool_line("ralph_read_file", {"path": "/tmp/x"}, status="error")

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.ERROR_LINE
    assert signal.raw == "MCP error -32001: Request timed out"


def test_opencode_strategy_classifies_tool_result_as_tool_result() -> None:
    """A ``tool_result`` envelope must not be counted as a second tool call."""
    strategy = strategy_for_transport(AgentTransport.OPENCODE)
    line = json.dumps(
        {
            "type": "tool_result",
            "sessionID": "ses_1",
            "part": {
                "type": "tool",
                "tool": "ralph_read_file",
                "callID": "call_1",
                "state": {"status": "completed", "input": {"path": "/tmp/x"}, "output": "ok"},
            },
        }
    )

    signal = strategy.classify_activity_line(line)

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_RESULT


def test_opencode_distinct_tool_calls_do_not_trip_the_breaker() -> None:
    """Eight DIFFERENT OpenCode tool calls MUST NOT trip the breaker.

    This is the production regression: the window rule (8 occurrences of one
    fingerprint in 600s) is deliberately immune to ``note_progress``, so once
    every call shared the ``("unknown", {})`` fingerprint, any OpenCode agent
    that used eight tools in ten minutes was killed mid-run.
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
    strategy = strategy_for_transport(AgentTransport.OPENCODE)
    tools = [
        ("ralph_read_file", {"path": "/tmp/a"}),
        ("ralph_exec", {"command": "ls"}),
        ("ralph_search_files", {"pattern": "**/x"}),
        ("ralph_list_directory", {"path": "/tmp"}),
        ("ralph_git_status", {"format": "compact"}),
        ("todowrite", {"todos": [{"content": "a"}]}),
        ("ralph_write_file", {"path": "/tmp/b"}),
        ("ralph_submit_md_artifact", {"artifact_type": "smoke_test_result"}),
    ]

    for index, (tool, tool_input) in enumerate(tools):
        line = _opencode_tool_line(tool, tool_input, call_id=f"call_{index}")
        signal = strategy.classify_activity_line(line)
        assert signal is not None
        assert signal.kind == AgentActivityKind.TOOL_USE
        extracted = extract_tool_call_from_activity_signal(signal.raw)
        assert extracted is not None
        watchdog.record_tool_call_activity(*extracted)
        clock.advance(2.0)

    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert verdict != WatchdogVerdict.FIRE


def test_opencode_identical_tool_calls_still_trip_the_breaker() -> None:
    """The breaker must stay REACHABLE on OpenCode: five identical calls fire."""
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
    strategy = strategy_for_transport(AgentTransport.OPENCODE)

    for index in range(5):
        line = _opencode_tool_line("ralph_exec", {"command": "ls"}, call_id=f"call_{index}")
        signal = strategy.classify_activity_line(line)
        assert signal is not None
        extracted = extract_tool_call_from_activity_signal(signal.raw)
        assert extracted is not None
        watchdog.record_tool_call_activity(*extracted)
        clock.advance(2.0)

    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL
    assert watchdog.repetition_diagnostic().get("tool_name") == "ralph_exec"
