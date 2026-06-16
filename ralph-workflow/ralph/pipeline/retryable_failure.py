"""Shared retryable agent-failure classification helpers."""

from __future__ import annotations

from typing import cast

from ralph.recovery.failure_classifier import (
    POST_TOOL_ACTIVITY_MARKERS,
    POST_TOOL_EMPTY_RESPONSE_SUBSTRINGS,
    SESSION_NOT_FOUND_SUBSTRINGS,
)
from ralph.recovery.failure_details import contains_casefolded_marker, failure_detail_parts

# Intentionally BROADER than ``failure_classifier._TRANSPORT_SUBSTRINGS``.
# This set is a coarse retry trigger: any of these substrings marks the failure
# as worth retrying as a transient connectivity fault. The classifier's
# environmental transport set deliberately OMITS bare ``timeout``/``timed out``
# so connectivity-aware timeouts stay agent-attributable (see
# tests/recovery/test_classifier_session.py). The two sets serve different
# decisions and must NOT be merged into one object.
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

_TURN_LIMIT_MARKERS = ("conversation exceeded 50 turns",)


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
            contains_casefolded_marker(detail_parts, SESSION_NOT_FOUND_SUBSTRINGS),
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
            contains_casefolded_marker(detail_parts, POST_TOOL_EMPTY_RESPONSE_SUBSTRINGS)
            and contains_casefolded_marker(detail_parts, POST_TOOL_ACTIVITY_MARKERS),
            "a post-tool-result continuation failure",
        ),
        # A plain empty model turn with no tool call (no prior tool activity) is
        # still a recoverable wedge: the model connected, produced no actionable
        # output, and another attempt may succeed. This mirrors the pipeline
        # classifier treating an empty response as a recoverable agent failure,
        # so the direct-MCP recovery path (commit/prompt-helper/smoke) does not
        # drift from the pipeline by hard-failing where the pipeline would retry.
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
