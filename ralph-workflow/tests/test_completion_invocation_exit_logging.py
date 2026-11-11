from __future__ import annotations

from loguru import logger

from ralph.agents.invoke._completion import (
    _extract_rejected_session_id_from_failure,
    log_invocation_exit,
)
from ralph.agents.invoke._errors import AgentInvocationError


def _capture_logs(exc: AgentInvocationError) -> str:
    captured: list[str] = []
    sink_id = logger.add(captured.append, level="TRACE", format="{message}")
    try:
        log_invocation_exit(exc)
    finally:
        logger.remove(sink_id)
    return "".join(captured)


def test_terminal_exit_logs_captured_output_evidence() -> None:
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
    exc = AgentInvocationError("nanocoder", 1, "boom", parsed_output=[])

    logged = _capture_logs(exc)

    assert "1" in logged
    assert "boom" in logged


def _capture_levels(exc: AgentInvocationError) -> list[str]:
    levels: list[str] = []

    def _sink(message: str) -> None:
        levels.append(message.strip())

    sink_id = logger.add(_sink, level="TRACE", format="{level}")
    try:
        log_invocation_exit(exc)
    finally:
        logger.remove(sink_id)
    return levels


def test_retryable_empty_response_logs_at_warning_not_error() -> None:
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
    exc = AgentInvocationError(
        "claude",
        1,
        "fatal unrecoverable failure",
        parsed_output=["nothing retryable here"],
    )

    levels = _capture_levels(exc)

    assert "ERROR" in levels


def _stale_session_exc() -> AgentInvocationError:
    return AgentInvocationError(
        "opencode",
        1,
        "Error: Session not found",
        parsed_output=[],
    )


def test_stale_session_exit_logs_warning_with_structured_phrase() -> None:
    exc = _stale_session_exc()

    levels = _capture_levels(exc)
    logged = _capture_logs(exc)

    assert "WARNING" in levels
    assert "stale session" in logged.lower()
    assert "resetting session" in logged.lower() or "fresh session" in logged.lower()


def test_stale_session_exit_surfaces_returncode_and_stderr() -> None:
    exc = _stale_session_exc()

    logged = _capture_logs(exc)

    assert "1" in logged
    assert "Error: Session not found" in logged
    assert "stale session" in logged.lower()


def test_stale_session_exit_suppresses_no_output_placeholder() -> None:
    exc = _stale_session_exc()

    logged = _capture_logs(exc)

    assert "(no output captured)" not in logged


def test_stale_session_exit_with_empty_stderr_does_not_crash_and_logs_meaningfully() -> None:
    exc = AgentInvocationError(
        "opencode",
        1,
        "",
        parsed_output=["Error: Session not found"],
    )

    logged = _capture_logs(exc)
    levels = _capture_levels(exc)

    assert "stale session" in logged.lower()
    assert "1" in logged
    assert "WARNING" in levels


def test_stale_session_exit_with_generic_stderr_surfaces_parsed_output_evidence() -> None:
    exc = AgentInvocationError(
        "opencode",
        1,
        "agent exited",
        parsed_output=["Error: Session not found"],
    )

    logged = _capture_logs(exc)
    levels = _capture_levels(exc)

    assert "stale session" in logged.lower()
    assert "resetting session" in logged.lower() or "fresh session" in logged.lower()
    assert "WARNING" in levels
    assert "ERROR" not in levels
    assert "Error: Session not found" in logged
    assert "(suppressed -- stderr already names the failure)" not in logged
    assert "agent exited" in logged
    assert "1" in logged


def test_stale_session_exit_logs_surfaces_rejected_session_id_when_present() -> None:
    exc = AgentInvocationError(
        "opencode",
        1,
        "Error: Session not found: development_analysis-c448ac22",
    )

    logged = _capture_logs(exc)
    levels = _capture_levels(exc)

    assert "WARNING" in levels
    assert "stale session" in logged.lower()
    assert "resetting session" in logged.lower() or "fresh session" in logged.lower()
    assert "development_analysis-c448ac22" in logged, (
        "stale-session WARNING line must surface the rejected session id "
        "so an operator can correlate the log line with the prior --session invocation"
    )


def test_stale_session_exit_logs_surfaces_rejected_session_id_for_id_label_variant() -> None:
    exc = AgentInvocationError(
        "opencode",
        1,
        "Error: Session not found for ID: opencode-stale-stderr-marker",
    )

    assert _extract_rejected_session_id_from_failure(exc) == "opencode-stale-stderr-marker", (
        "_extract_rejected_session_id_from_failure must extract the id from the "
        "'Session not found for ID: <id>' label-separated variant"
    )

    logged = _capture_logs(exc)
    levels = _capture_levels(exc)

    assert "WARNING" in levels
    assert "stale session" in logged.lower()
    assert "resetting session" in logged.lower() or "fresh session" in logged.lower()
    assert "session_id=opencode-stale-stderr-marker" in logged, (
        "stale-session WARNING line must surface the dedicated "
        "session_id=opencode-stale-stderr-marker field for the "
        "'Session not found for ID: <id>' label-separated variant; "
        f"got: {logged!r}"
    )


def test_stale_session_exit_logs_surfaces_rejected_session_id_with_dot_and_colon_chars() -> None:
    exc = AgentInvocationError(
        "opencode",
        1,
        "Error: Session not found: abc.def:ghi",
    )

    assert _extract_rejected_session_id_from_failure(exc) == "abc.def:ghi", (
        "_extract_rejected_session_id_from_failure must surface session ids "
        "containing '.' and ':' (the canonical session-id character class in "
        "_session.py); got None for 'Error: Session not found: abc.def:ghi'"
    )

    logged = _capture_logs(exc)
    levels = _capture_levels(exc)

    assert "WARNING" in levels
    assert "stale session" in logged.lower()
    assert "resetting session" in logged.lower() or "fresh session" in logged.lower()
    assert "session_id=abc.def:ghi" in logged, (
        "stale-session WARNING line must surface the dedicated "
        "session_id=abc.def:ghi field for ids containing '.' and ':'; "
        f"got: {logged!r}"
    )


def test_stale_session_exit_with_no_session_id_marker_does_not_emit_session_id_field() -> None:
    exc = AgentInvocationError(
        "opencode",
        1,
        "Some unrelated connectivity failure",
        parsed_output=["just a regular error", "no session marker here"],
    )

    assert _extract_rejected_session_id_from_failure(exc) is None, (
        "_extract_rejected_session_id_from_failure must return None for non-stale payloads"
    )

    logged = _capture_logs(exc)

    assert "session_id=" not in logged, (
        "stale-session WARNING line must omit session_id= when the failure has no "
        f"canonical SESSION_NOT_FOUND_SUBSTRINGS marker; got: {logged!r}"
    )


def _transcript(lines: int) -> list[str]:
    return [
        f'{{"type":"item.completed","item":{{"id":"item_{i}","text":"{"x" * 400}"}}}}'
        for i in range(lines)
    ]


def test_terminal_exit_bounds_a_huge_transcript() -> None:
    parsed = [*_transcript(60), "Model returned an empty response with no tool calls"]
    exc = AgentInvocationError("codex", 143, "", parsed_output=parsed)

    logged = _capture_logs(exc)

    assert "Model returned an empty response with no tool calls" in logged
    assert "item_0" not in logged
    assert "earlier lines omitted" in logged
    assert len(logged) < 1500


def test_terminal_exit_bounds_a_huge_stderr() -> None:
    exc = AgentInvocationError("codex", 1, "boom\n" + "y" * 20_000, parsed_output=[])

    logged = _capture_logs(exc)

    assert "boom" in logged
    assert "\n" not in logged.strip()
    assert len(logged) < 1500
