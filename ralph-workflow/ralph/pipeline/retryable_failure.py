"""Shared retryable agent-failure classification helpers."""

from __future__ import annotations

from typing import cast

from ralph.recovery.failure_details import contains_casefolded_marker, failure_detail_parts

_TRANSIENT_CONNECTIVITY_MARKERS = (
    "connection refused",
    "network is unreachable",
    "temporary failure in name resolution",
    "name or service not known",
    "timed out",
    "timeout",
    "offline",
    "econnreset",
    "enotfound",
    "socket hang up",
)

_TURN_LIMIT_MARKERS = (
    "conversation exceeded 50 turns",
)

_POST_TOOL_EMPTY_RESPONSE_MARKERS = (
    "empty response with no tool calls",
    "empty response",
)

_POST_TOOL_ACTIVITY_MARKERS = (
    '"type":"tool_result"',
    '"type": "tool_result"',
    '"type":"mcp_tool_result"',
    '"type": "mcp_tool_result"',
    '"type":"tool_use"',
    '"type": "tool_use"',
    "[plain] tool:",
    " tool: ",
)

_SESSION_NOT_FOUND_SUBSTRINGS = (
    "No conversation found with session ID:",
    "Session not found",
    "Unknown session",
    "session does not exist",
)


def retryable_agent_failure_reason(
    exc: Exception,
    inactivity_error_type: type[Exception],
) -> str | None:
    """Return the canonical retry reason for a retryable agent failure."""
    detail_parts = failure_detail_parts(exc)
    primary_text = _primary_recovery_error_text(exc)
    checks: tuple[tuple[bool, str], ...] = (
        (isinstance(exc, inactivity_error_type), "an inactivity timeout"),
        (
            type(exc).__name__ == "OpenCodeResumableExitError",
            "agent session exited without required completion evidence",
        ),
        (
            contains_casefolded_marker(detail_parts, _SESSION_NOT_FOUND_SUBSTRINGS),
            "a stale session ID (fresh session required)",
        ),
        (
            contains_casefolded_marker(detail_parts, _TURN_LIMIT_MARKERS),
            "the agent conversation turn limit",
        ),
        (
            _has_transient_connectivity_signal(primary_text),
            "a transient connectivity failure",
        ),
        (
            contains_casefolded_marker(detail_parts, _POST_TOOL_EMPTY_RESPONSE_MARKERS)
            and contains_casefolded_marker(detail_parts, _POST_TOOL_ACTIVITY_MARKERS),
            "a post-tool-result continuation failure",
        ),
    )
    for matched, reason in checks:
        if matched:
            return reason
    return None


def _primary_recovery_error_text(exc: Exception) -> str:
    stderr = cast("object", getattr(exc, "stderr", ""))
    if isinstance(stderr, str) and stderr.strip():
        return stderr
    return str(exc)


def _has_transient_connectivity_signal(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _TRANSIENT_CONNECTIVITY_MARKERS)


__all__ = ["retryable_agent_failure_reason"]
