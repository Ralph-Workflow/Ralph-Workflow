from __future__ import annotations

import re
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._direct_mcp_recovery import (
    condense_failure_text,
    summarize_retry_failure_evidence,
)
from ralph.pipeline import retryable_failure as _retry
from ralph.recovery.failure_classifier import SESSION_NOT_FOUND_SUBSTRINGS, classify_failure
from ralph.recovery.failure_details import contains_casefolded_marker

if TYPE_CHECKING:
    from ralph.agents.invoke._errors import AgentInvocationError

_SESSION_ID_PATTERN = r"[A-Za-z0-9._:\-]{4,}"


def _extract_rejected_session_id_from_failure(exc: AgentInvocationError) -> str | None:
    _marker_pattern = "|".join(re.escape(m) for m in SESSION_NOT_FOUND_SUBSTRINGS)
    _pattern = re.compile(
        rf"(?i)(?:{_marker_pattern})"
        rf"(?:\s+(?:for\s+ID|for\s+id|ID|id))?"
        rf"[\s:]+({_SESSION_ID_PATTERN})",
    )
    haystack = [exc.stderr] if exc.stderr else []
    haystack.extend(exc.parsed_output)
    for line in haystack:
        if not line:
            continue
        match = _pattern.search(line)
        if match is not None:
            return match.group(1)
    return None


def log_invocation_exit(exc: AgentInvocationError) -> None:
    classified = classify_failure(exc, phase="invoke", agent=exc.agent_name)
    retryable = _retry.retryable_agent_failure_reason(exc, AgentInactivityTimeoutError) is not None
    if classified.reset_session:
        stderr_has_session_marker = contains_casefolded_marker(
            [exc.stderr] if exc.stderr else [], SESSION_NOT_FOUND_SUBSTRINGS
        )
        evidence_field = (
            "(suppressed -- stderr already names the failure)"
            if stderr_has_session_marker
            else summarize_retry_failure_evidence(exc.parsed_output)
        )
        stderr_field = condense_failure_text(exc.stderr) if exc.stderr.strip() else "(empty)"
        rejected_session_id = _extract_rejected_session_id_from_failure(exc)
        if rejected_session_id is not None:
            logger.warning(
                "Stale session detected for agent={} (phase=invoke): "
                "resetting session id, retrying with a fresh session. "
                "code={} stderr={} evidence=[{}] session_id={}",
                exc.agent_name,
                exc.returncode,
                stderr_field,
                evidence_field,
                rejected_session_id,
            )
            return
        logger.warning(
            "Stale session detected for agent={} (phase=invoke): "
            "resetting session id, retrying with a fresh session. "
            "code={} stderr={} evidence=[{}]",
            exc.agent_name,
            exc.returncode,
            stderr_field,
            evidence_field,
        )
        return
    if classified.reset_tool_registry or retryable:
        logger.warning(
            "Retryable agent exit with code {}: {} [{}]",
            exc.returncode,
            condense_failure_text(exc.stderr),
            summarize_retry_failure_evidence(exc.parsed_output),
        )
        return
    logger.error(
        "Agent exited with code {}: {} [{}]",
        exc.returncode,
        condense_failure_text(exc.stderr),
        summarize_retry_failure_evidence(exc.parsed_output),
    )


__all__ = ["_extract_rejected_session_id_from_failure", "log_invocation_exit"]
