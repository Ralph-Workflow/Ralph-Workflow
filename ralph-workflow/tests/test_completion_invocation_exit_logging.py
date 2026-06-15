"""Regression tests: terminal agent-exit logging must preserve captured output.

Part of the diagnosability bug family. When an agent (e.g. nanocoder running a
commit) exits non-zero with a NON-retryable failure, the captured agent output
(``parsed_output``) is the only evidence of WHY it failed — e.g. the line
"Model returned an empty response with no tool calls". The retryable branch
logged that evidence; the terminal branch dropped it, logging only stderr. These
tests pin that both branches surface the captured output evidence.
"""

from __future__ import annotations

from loguru import logger

from ralph.agents.invoke._completion import _log_invocation_exit
from ralph.agents.invoke._errors import AgentInvocationError


def _capture_logs(exc: AgentInvocationError) -> str:
    captured: list[str] = []
    sink_id = logger.add(captured.append, level="TRACE", format="{message}")
    try:
        _log_invocation_exit(exc)
    finally:
        logger.remove(sink_id)
    return "".join(captured)


def test_terminal_exit_logs_captured_output_evidence() -> None:
    """A non-retryable agent exit must log the captured output, not just stderr."""
    exc = AgentInvocationError(
        "nanocoder",
        1,
        "[plain] MCP server connected: ralph",
        parsed_output=[
            "nanocoder raw: reasoning about the commit message ...",
            "Model returned an empty response with no tool calls",
        ],
    )

    logged = _capture_logs(exc)

    assert "Model returned an empty response with no tool calls" in logged


def test_terminal_exit_with_no_output_logs_gracefully() -> None:
    """A terminal exit with no captured output must still log without error."""
    exc = AgentInvocationError("nanocoder", 1, "boom", parsed_output=[])

    logged = _capture_logs(exc)

    assert "1" in logged
    assert "boom" in logged


def _capture_levels(exc: AgentInvocationError) -> list[str]:
    levels: list[str] = []
    sink_id = logger.add(lambda message: levels.append(message.record["level"].name), level="TRACE")
    try:
        _log_invocation_exit(exc)
    finally:
        logger.remove(sink_id)
    return levels


def test_retryable_empty_response_logs_at_warning_not_error() -> None:
    """A retryable empty-response exit logs at WARNING, reflecting its disposition."""
    exc = AgentInvocationError(
        "nanocoder",
        1,
        "agent exited",
        parsed_output=["Model returned an empty response with no tool calls"],
    )

    levels = _capture_levels(exc)

    assert "WARNING" in levels
    assert "ERROR" not in levels


def test_truly_terminal_failure_logs_at_error() -> None:
    """A non-retryable failure logs at ERROR."""
    exc = AgentInvocationError(
        "claude",
        1,
        "fatal unrecoverable failure",
        parsed_output=["nothing retryable here"],
    )

    levels = _capture_levels(exc)

    assert "ERROR" in levels
