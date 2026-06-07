"""Shared direct-invocation recovery for post-tool MCP continuation failures."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

from ralph.agents.invoke._agent_invocation_error import AgentInvocationError
from ralph.recovery.failure_classifier import FailureClassifier

from ._session import extract_transport_session_id

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator


_T = TypeVar("_T")
_MAX_RECOVERY_ATTEMPT_LINES = 400
@dataclass(frozen=True)
class _DirectMcpRetryPlan:
    session_id: str
    reset_tool_registry: bool


def default_direct_mcp_retry_limit(raw_limit: object) -> int:
    if isinstance(raw_limit, int) and raw_limit >= 0:
        return raw_limit
    return 10


def _retry_plan_for_agent_invocation_error(
    exc: AgentInvocationError,
    *,
    attempt_lines: list[str],
    current_session_id: str | None,
) -> _DirectMcpRetryPlan | None:
    classified = FailureClassifier().classify(exc, phase="standalone", agent=exc.agent_name)
    if not classified.reset_tool_registry:
        return None
    session_id = (
        extract_transport_session_id(tuple(attempt_lines))
        or extract_transport_session_id(tuple(exc.parsed_output))
        or current_session_id
    )
    if not session_id:
        return None
    return _DirectMcpRetryPlan(session_id=session_id, reset_tool_registry=True)


def run_with_direct_mcp_recovery(
    run_attempt: "Callable[[str | None], _T]",
    *,
    max_retries: int,
    reset_tool_registry: "Callable[[], object] | None" = None,
    on_retry_failure: "Callable[[list[str]], object] | None" = None,
) -> _T:
    current_session_id: str | None = None
    retries_used = 0
    while True:
        try:
            return run_attempt(current_session_id)
        except AgentInvocationError as exc:
            if reset_tool_registry is None or retries_used >= max_retries:
                raise
            retry_plan = _retry_plan_for_agent_invocation_error(
                exc,
                attempt_lines=list(exc.parsed_output),
                current_session_id=current_session_id,
            )
            if retry_plan is None:
                raise
            if on_retry_failure is not None:
                on_retry_failure(list(exc.parsed_output))
            if retry_plan.reset_tool_registry:
                reset_tool_registry()
            current_session_id = retry_plan.session_id
            retries_used += 1


def iter_with_direct_mcp_recovery(
    run_attempt: "Callable[[str | None], Iterable[str]]",
    *,
    max_retries: int,
    reset_tool_registry: "Callable[[], object] | None" = None,
) -> "Iterator[str]":
    current_session_id: str | None = None
    retries_used = 0
    while True:
        attempt_lines: deque[str] = deque(maxlen=_MAX_RECOVERY_ATTEMPT_LINES)
        try:
            for line in run_attempt(current_session_id):
                attempt_lines.append(line)
                yield line
            return
        except AgentInvocationError as exc:
            if reset_tool_registry is None or retries_used >= max_retries:
                raise _invocation_error_with_output(exc, attempt_lines)
            exc_with_output = _invocation_error_with_output(exc, attempt_lines)
            retry_plan = _retry_plan_for_agent_invocation_error(
                exc_with_output,
                attempt_lines=list(attempt_lines),
                current_session_id=current_session_id,
            )
            if retry_plan is None:
                raise exc_with_output
            if retry_plan.reset_tool_registry:
                reset_tool_registry()
            current_session_id = retry_plan.session_id
            retries_used += 1


def _invocation_error_with_output(
    exc: AgentInvocationError,
    attempt_lines: list[str] | deque[str],
) -> AgentInvocationError:
    if attempt_lines:
        return AgentInvocationError(
            exc.agent_name,
            exc.returncode,
            exc.stderr,
            parsed_output=list(attempt_lines),
        )
    return exc


__all__ = [
    "default_direct_mcp_retry_limit",
    "iter_with_direct_mcp_recovery",
    "run_with_direct_mcp_recovery",
]
