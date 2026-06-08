"""Shared direct-invocation recovery for post-tool MCP continuation failures."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._agent_invocation_error import AgentInvocationError
from ralph.pipeline.agent_retry_intent import agent_retry_intent_for_failure
from ralph.pipeline.retryable_failure import retryable_agent_failure_reason
from ralph.recovery.failure_classifier import FailureClassifier

from ._session import extract_transport_session_id, extract_transport_session_id_from_line

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

_MAX_RECOVERY_ATTEMPT_LINES = 400
_RETRY_FAILURE_EVIDENCE_LINES = 5


@dataclass(frozen=True)
class _DirectMcpRetryPlan:
    session_id: str | None
    reset_tool_registry: bool


def default_direct_mcp_retry_limit(raw_limit: object) -> int:
    if isinstance(raw_limit, int) and raw_limit >= 0:
        return raw_limit
    return 10


def _exception_agent_name(exc: Exception) -> str | None:
    attributes = cast("dict[str, object]", vars(exc))
    raw = attributes.get("agent_name")
    return raw if isinstance(raw, str) and raw else None


def _exception_parsed_output(exc: Exception) -> tuple[str, ...]:
    attributes = cast("dict[str, object]", vars(exc))
    raw = attributes.get("parsed_output", ())
    if not isinstance(raw, list | tuple):
        return ()
    return tuple(str(line) for line in raw)


def _retry_plan_for_exception(
    exc: Exception,
    *,
    attempt_lines: list[str],
    current_session_id: str | None,
) -> _DirectMcpRetryPlan | None:
    if retryable_agent_failure_reason(exc, AgentInactivityTimeoutError) is None:
        return None
    classified = FailureClassifier().classify(
        exc,
        phase="standalone",
        agent=_exception_agent_name(exc),
    )
    session_id = (
        extract_transport_session_id(tuple(attempt_lines))
        or extract_transport_session_id(_exception_parsed_output(exc))
        or current_session_id
    )
    retry_intent = agent_retry_intent_for_failure(
        failure_reason=str(exc),
        session_id=session_id,
        reset_tool_registry=classified.reset_tool_registry,
    )
    return _DirectMcpRetryPlan(
        session_id=retry_intent.session_id,
        reset_tool_registry=retry_intent.reset_tool_registry,
    )


def run_with_direct_mcp_recovery[T](
    run_attempt: Callable[[str | None, Callable[[str], None]], T],
    *,
    max_retries: int,
    reset_tool_registry: Callable[[], object] | None = None,
    on_retry_failure: Callable[[list[str]], object] | None = None,
    on_session_observed: Callable[[str], object] | None = None,
) -> T:
    current_session_id: str | None = None
    retries_used = 0
    while True:
        observed_session_id = current_session_id

        def _capture_session_id(session_id: str) -> None:
            nonlocal observed_session_id
            observed_session_id = session_id
            if on_session_observed is not None:
                on_session_observed(session_id)

        try:
            return run_attempt(current_session_id, _capture_session_id)
        except Exception as exc:
            if type(exc).__name__ == "OpenCodeResumableExitError":
                raise
            if reset_tool_registry is None or retries_used >= max_retries:
                raise
            retry_plan = _retry_plan_for_exception(
                exc,
                attempt_lines=list(_exception_parsed_output(exc)),
                current_session_id=observed_session_id,
            )
            if retry_plan is None:
                raise
            if on_retry_failure is not None:
                on_retry_failure(list(_exception_parsed_output(exc)))
            if retry_plan.reset_tool_registry:
                reset_tool_registry()
            current_session_id = retry_plan.session_id
            retries_used += 1


def iter_with_direct_mcp_recovery(
    run_attempt: Callable[[str | None], Iterable[str]],
    *,
    max_retries: int,
    reset_tool_registry: Callable[[], object] | None = None,
    on_retry_failure: Callable[[list[str]], object] | None = None,
    on_session_observed: Callable[[str], object] | None = None,
) -> Iterator[str]:
    current_session_id: str | None = None
    retries_used = 0
    while True:
        attempt_lines: deque[str] = deque(maxlen=_MAX_RECOVERY_ATTEMPT_LINES)
        try:
            for line in run_attempt(current_session_id):
                attempt_lines.append(line)
                observed_session_id = extract_transport_session_id_from_line(line)
                if observed_session_id is not None:
                    current_session_id = observed_session_id
                    if on_session_observed is not None:
                        on_session_observed(observed_session_id)
                yield line
            return
        except Exception as exc:
            if type(exc).__name__ == "OpenCodeResumableExitError":
                raise
            if reset_tool_registry is None or retries_used >= max_retries:
                raise _invocation_error_with_output(exc, attempt_lines) from exc
            exc_with_output = _invocation_error_with_output(exc, attempt_lines)
            retry_plan = _retry_plan_for_exception(
                exc_with_output,
                attempt_lines=list(attempt_lines),
                current_session_id=current_session_id,
            )
            if retry_plan is None:
                raise exc_with_output from exc
            if on_retry_failure is not None:
                on_retry_failure(list(_exception_parsed_output(exc_with_output)))
            if retry_plan.reset_tool_registry:
                reset_tool_registry()
            current_session_id = retry_plan.session_id
            retries_used += 1


def _invocation_error_with_output(
    exc: Exception,
    attempt_lines: list[str] | deque[str],
) -> Exception:
    if not isinstance(exc, AgentInvocationError):
        return exc
    merged_lines: list[str] = list(attempt_lines)
    for line in exc.parsed_output:
        if line not in merged_lines:
            merged_lines.append(line)
    if merged_lines:
        return AgentInvocationError(
            exc.agent_name,
            exc.returncode,
            exc.stderr,
            parsed_output=merged_lines,
        )
    return exc


def summarize_retry_failure_evidence(lines: list[str]) -> str:
    meaningful = [line.strip() for line in lines if line.strip()]
    if not meaningful:
        return "(no output captured)"
    return " | ".join(meaningful)


__all__ = [
    "default_direct_mcp_retry_limit",
    "iter_with_direct_mcp_recovery",
    "run_with_direct_mcp_recovery",
    "summarize_retry_failure_evidence",
]
