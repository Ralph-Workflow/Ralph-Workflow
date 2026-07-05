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


def _stale_session_exc() -> AgentInvocationError:
    """Return the exact AgentInvocationError shape that triggered the user-visible
    opaque log line ``Retryable agent exit with code 1: Error: Session not found
    [(no output captured)]``. The classifier routes this to
    ``reset_session=True`` via ``SESSION_NOT_FOUND_SUBSTRINGS``, but the legacy
    retryable WARNING branch drops the recovery-action signal and appends the
    misleading "(no output captured)" suffix even though stderr already names
    the failure. These tests pin a structured, operator-friendly log line."""
    return AgentInvocationError(
        "opencode",
        1,
        "Error: Session not found",
        parsed_output=[],
    )


def test_stale_session_exit_logs_warning_with_structured_phrase() -> None:
    """Stale-session exits log at WARNING with the phrase 'stale session' and
    the recovery action ('resetting session' / 'fresh session') so an operator
    can see this is a stale-session recovery, not a generic retry."""
    exc = _stale_session_exc()

    levels = _capture_levels(exc)
    logged = _capture_logs(exc)

    assert "WARNING" in levels
    assert "stale session" in logged.lower()
    assert "resetting session" in logged.lower() or "fresh session" in logged.lower()


def test_stale_session_exit_surfaces_returncode_and_stderr() -> None:
    """The structured log line must surface the returncode AND the original
    stderr text so the original evidence is never dropped -- AND the legacy
    'Retryable agent exit' phrasing is replaced by the structured stale-session
    phrasing so the operator can grep/alert on the recovery branch."""
    exc = _stale_session_exc()

    logged = _capture_logs(exc)

    assert "1" in logged
    assert "Error: Session not found" in logged
    assert "stale session" in logged.lower()


def test_stale_session_exit_suppresses_no_output_placeholder() -> None:
    """When stderr already names the failure ("Error: Session not found"), the
    misleading "(no output captured)" placeholder must NOT appear in the log
    line -- appending it next to meaningful stderr is confusing and false."""
    exc = _stale_session_exc()

    logged = _capture_logs(exc)

    assert "(no output captured)" not in logged


def test_stale_session_exit_with_empty_stderr_does_not_crash_and_logs_meaningfully() -> None:
    """A stale-session exit with empty stderr (rare but possible -- the
    classifier also matches against parsed_output, so a marker carried in
    parsed_output triggers reset_session=True even when stderr is empty) must
    NOT crash _log_invocation_exit and must still surface 'stale session' and
    the returncode."""
    exc = AgentInvocationError(
        "opencode",
        1,
        "",
        parsed_output=["Error: Session not found"],
    )

    # Must not raise
    logged = _capture_logs(exc)
    levels = _capture_levels(exc)

    assert "stale session" in logged.lower()
    assert "1" in logged
    assert "WARNING" in levels


def test_stale_session_exit_with_generic_stderr_surfaces_parsed_output_evidence() -> None:
    """Mixed case: ``reset_session=True`` is inferred from a stale-session
    marker carried in ``parsed_output`` (e.g. an agent that prints
    ``Error: Session not found`` to stdout while stderr says something
    generic like ``agent exited``). The structured stale-session log line
    must STILL surface the parsed-output evidence -- the generic stderr does
    not name the failure, so suppressing the parsed_output evidence here
    would drop the only concrete stale-session clue and the operator would
    see the misleading ``(suppressed -- stderr already names the failure)``
    placeholder next to a stderr that doesn't actually name anything."""
    exc = AgentInvocationError(
        "opencode",
        1,
        "agent exited",
        parsed_output=["Error: Session not found"],
    )

    logged = _capture_logs(exc)
    levels = _capture_levels(exc)

    # The structured stale-session recovery branch is taken.
    assert "stale session" in logged.lower()
    assert "resetting session" in logged.lower() or "fresh session" in logged.lower()
    assert "WARNING" in levels
    assert "ERROR" not in levels
    # The parsed_output evidence is surfaced (NOT suppressed) because the
    # generic stderr does not actually name a stale-session marker.
    assert "Error: Session not found" in logged
    assert "(suppressed -- stderr already names the failure)" not in logged
    # The generic stderr is still surfaced too, for completeness.
    assert "agent exited" in logged
    assert "1" in logged
