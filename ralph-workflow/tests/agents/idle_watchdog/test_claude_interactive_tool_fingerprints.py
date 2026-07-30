"""The default ``claude`` agent must fingerprint tool calls by their arguments.

``builtin.py`` registers ``claude`` on ``AgentTransport.CLAUDE_INTERACTIVE``,
whose strategy intercepts before the JSON classifier. It used to hand the
watchdog the operator-facing ``claude tool: <name>`` marker, which carries no
arguments -- so ten DIFFERENT Bash commands all fingerprinted as ``Bash|{}``
and fired ``REPEATED_IDENTICAL_TOOL_CALL`` on a healthy agent. Because this is
the default agent, that false kill was the common case.
"""

from __future__ import annotations

import json

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import AgentExecutionState, strategy_for_transport
from ralph.agents.idle_watchdog import IdleWatchdog, WatchdogVerdict
from ralph.agents.idle_watchdog.timeout_policy import TimeoutPolicy
from ralph.agents.invoke._tool_call_extraction import extract_tool_call_from_activity_signal
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport


def _transcript_line(command: str) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Bash",
                        "input": {"command": command},
                    }
                ]
            },
        }
    )


def test_interactive_tool_use_carries_its_arguments() -> None:
    """The transcript parser already has ``input``; it must reach the breaker."""
    strategy = strategy_for_transport(AgentTransport.CLAUDE_INTERACTIVE)

    signal = strategy.classify_activity_line(_transcript_line("git status --short"))

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE
    assert extract_tool_call_from_activity_signal(signal.raw) == (
        "Bash",
        {"command": "git status --short"},
    )


def test_interactive_distinct_commands_do_not_trip_the_breaker() -> None:
    """Ten different Bash commands MUST NOT look like one wedged call."""
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
    strategy = strategy_for_transport(AgentTransport.CLAUDE_INTERACTIVE)
    commands = [
        "git status --short",
        "uv run pytest -q",
        "make lint",
        "ls ralph/display",
        "git diff --stat",
        "make verify",
        "cat pyproject.toml",
        "git log --oneline -5",
        "uv run ruff check ralph/",
        "make typecheck",
    ]

    for command in commands:
        signal = strategy.classify_activity_line(_transcript_line(command))
        assert signal is not None
        extracted = extract_tool_call_from_activity_signal(signal.raw)
        assert extracted is not None
        watchdog.record_tool_call_activity(*extracted)
        clock.advance(30.0)

    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) != (
        WatchdogVerdict.FIRE
    )


def test_interactive_marker_without_metadata_still_falls_back() -> None:
    """A bare ``claude tool:`` marker keeps working through the plain branch."""
    strategy = strategy_for_transport(AgentTransport.CLAUDE_INTERACTIVE)

    signal = strategy.classify_activity_line("claude tool: read_file\n")

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE
    assert extract_tool_call_from_activity_signal(signal.raw) == ("read_file", {})
