
from __future__ import annotations

from typing import cast

from ralph.agents.invoke._agent_launch_error import AgentLaunchError
from ralph.recovery.failure_classifier import (
    POST_TOOL_ACTIVITY_MARKERS,
    POST_TOOL_EMPTY_RESPONSE_SUBSTRINGS,
    SESSION_NOT_FOUND_SUBSTRINGS,
    _is_subscription_limit_message,
)
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
    "fetch failed",
)

_TURN_LIMIT_MARKERS = ("conversation exceeded 50 turns",)

_MCP_TOOL_CALL_TIMEOUT_MARKERS = ("timed out awaiting tools/call",)


def retryable_agent_failure_reason(
    exc: Exception,
    inactivity_error_type: type[Exception],
) -> str | None:
    detail_parts = failure_detail_parts(exc)
    if type(exc).__name__ == "QuotaExhaustedError" or _is_subscription_limit_message(detail_parts):
        return None
    primary_text = _primary_recovery_error_text(exc)
    checks: tuple[tuple[bool, str], ...] = (
        (isinstance(exc, AgentLaunchError), "a transient runtime launch failure"),
        (isinstance(exc, inactivity_error_type), "an inactivity timeout"),
        (
            type(exc).__name__ == "OpenCodeResumableExitError",
            "agent session exited without required completion evidence",
        ),
        (
            type(exc).__name__ == "AgyIncompleteExitError",
            "the agent exited without required completion evidence",
        ),
        (
            contains_casefolded_marker(detail_parts, SESSION_NOT_FOUND_SUBSTRINGS),
            "a stale session ID (fresh session required)",
        ),
        (
            contains_casefolded_marker(detail_parts, _TURN_LIMIT_MARKERS),
            "the agent conversation turn limit",
        ),
        (
            contains_casefolded_marker(detail_parts, _MCP_TOOL_CALL_TIMEOUT_MARKERS),
            "a Ralph MCP tool call that timed out",
        ),
        (
            _has_transient_connectivity_signal(primary_text),
            "a transient connectivity failure",
        ),
        (
            contains_casefolded_marker(detail_parts, POST_TOOL_EMPTY_RESPONSE_SUBSTRINGS)
            and contains_casefolded_marker(detail_parts, POST_TOOL_ACTIVITY_MARKERS),
            "a post-tool-result continuation failure",
        ),
        (
            contains_casefolded_marker(detail_parts, POST_TOOL_EMPTY_RESPONSE_SUBSTRINGS),
            "an empty model response",
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
