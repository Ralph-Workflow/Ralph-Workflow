"""Shared direct-invocation recovery with an invocation-local retry budget.

Agent health, demotion, and agent selection belong exclusively to
:class:`ralph.recovery.agent_failure_ledger.AgentFailureLedger`. This module
only bounds the automatic attempts made within one direct invocation.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._agent_invocation_error import AgentInvocationError
from ralph.agents.invoke._supervision_infrastructure_error import SupervisionInfrastructureError
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.pipeline.agent_retry_decision import resolve_retry_intent
from ralph.runtime_events import RuntimeEventRecorder, record_runtime_event, runtime_event_scope

from ._session import (
    extract_transport_session_id,
    extract_transport_session_id_with_visible_tui,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

_MAX_RECOVERY_ATTEMPT_LINES = 400


@dataclass(frozen=True)
class _DirectMcpRetryPlan:
    session_id: str | None
    reset_tool_registry: bool
    skip_same_agent_retries: bool


def default_direct_mcp_retry_limit(raw_limit: object) -> int:
    """Return the automatic-retry budget for one direct invocation.

    Callers normally supply ``general.max_same_agent_retries`` (default 10).
    The value bounds only the attempts inside one invocation; it never decides
    whether an agent is healthy, should be demoted, or should be replaced.
    """

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


def _retry_budget_exhausted_error(
    exc: Exception,
    *,
    attempts_used: int,
    retries_used: int,
) -> Exception:
    """Attach a factual invocation-budget diagnostic without changing failure type."""

    diagnostic = (
        "Direct-MCP invocation retry budget exhausted after "
        f"{attempts_used} attempts ({retries_used} retries); "
        f"cause: {type(exc).__name__}."
    )
    if isinstance(exc, AgentInvocationError):
        exc.parsed_output = [*_exception_parsed_output(exc), diagnostic]
    else:
        exc.add_note(diagnostic)
    return exc


def _invocation_retry_allowed_or_raise(
    exc: Exception,
    *,
    reset_tool_registry: Callable[[], object] | None,
    retries_used: int,
    max_retries: int,
) -> bool:
    """Return whether this invocation may make another technical retry.

    ``max_retries`` is the invocation-local budget (normally
    ``general.max_same_agent_retries``). Exhausting it stops this invocation
    only; it is not an agent-health verdict or an agent-selection decision.
    """

    if reset_tool_registry is None:
        return False
    if retries_used < max_retries:
        return True
    raise _retry_budget_exhausted_error(
        exc,
        attempts_used=retries_used + 1,
        retries_used=retries_used,
    )


def _mcp_operation_error(exc: Exception, mcp_exc: Exception) -> AgentInvocationError:
    """Attach the recovery boundary when its registry operation fails."""
    if isinstance(exc, AgentInvocationError):
        return AgentInvocationError(
            exc.agent_name,
            exc.returncode,
            str(mcp_exc),
            parsed_output=exc.parsed_output,
            failure_origin="mcp_operation" if exc.failure_origin == "agent" else exc.failure_origin,
            issuer="direct_mcp_recovery.reset_tool_registry",
        )
    return AgentInvocationError(
        "mcp",
        1,
        str(mcp_exc),
        failure_origin="mcp_operation",
        issuer="direct_mcp_recovery.reset_tool_registry",
    )


def _retry_plan_for_exception(
    exc: Exception,
    *,
    attempt_lines: list[str],
    current_session_id: str | None,
) -> _DirectMcpRetryPlan | None:
    raw_resumable_session_id: object = getattr(exc, "resumable_session_id", None)
    resumable_session_id = (
        raw_resumable_session_id
        if isinstance(raw_resumable_session_id, str) and raw_resumable_session_id
        else None
    )
    session_id = (
        extract_transport_session_id(tuple(attempt_lines))
        or extract_transport_session_id(_exception_parsed_output(exc))
        or resumable_session_id
        or current_session_id
    )
    intent = resolve_retry_intent(
        exc,
        phase="standalone",
        agent=_exception_agent_name(exc),
        session_id=session_id,
        inactivity_error_type=AgentInactivityTimeoutError,
    )
    if intent is None:
        return None
    return _DirectMcpRetryPlan(
        session_id=intent.session_id,
        reset_tool_registry=intent.reset_tool_registry,
        skip_same_agent_retries=intent.skip_same_agent_retries,
    )


def _run_with_direct_mcp_recovery[T](
    run_attempt: Callable[[str | None, Callable[[str], None]], T],
    *,
    max_retries: int,
    reset_tool_registry: Callable[[], object] | None = None,
    on_retry_failure: Callable[[list[str]], object] | None = None,
    on_session_observed: Callable[[str], object] | None = None,
    retry_resumable_exit: bool = False,
) -> T:
    current_session_id: str | None = None
    retries_used = 0
    # One-reprompt bound (enforcement point 2 of 2) for
    # ``AgyIncompleteExitError``: an AGY invocation gets exactly ONE
    # automatic completion reprompt. The plan-level bound in
    # ``build_agent_recovery_plan`` is enforcement point 1; this loop
    # bound keeps non-pipeline callers (commit plumbing, session
    # runtime) on the same invariant.
    agy_incomplete_exit_reprompted = False
    while True:
        observed_session_id = current_session_id

        def _capture_session_id(session_id: str) -> None:
            nonlocal observed_session_id
            observed_session_id = session_id
            if on_session_observed is not None:
                on_session_observed(session_id)

        try:
            return run_attempt(current_session_id, _capture_session_id)
        except SupervisionInfrastructureError:
            raise
        except McpServerError:
            raise
        except Exception as exc:
            if type(exc).__name__ == "OpenCodeResumableExitError" and not retry_resumable_exit:
                raise
            if type(exc).__name__ == "AgyIncompleteExitError":
                if agy_incomplete_exit_reprompted:
                    raise
                agy_incomplete_exit_reprompted = True
            if not _invocation_retry_allowed_or_raise(
                exc,
                reset_tool_registry=reset_tool_registry,
                retries_used=retries_used,
                max_retries=max_retries,
            ):
                raise
            if reset_tool_registry is None:
                raise RuntimeError("retry registry unexpectedly unavailable") from exc
            retry_plan = _retry_plan_for_exception(
                exc,
                attempt_lines=list(_exception_parsed_output(exc)),
                current_session_id=observed_session_id,
            )
            if retry_plan is None or retry_plan.skip_same_agent_retries:
                raise
            if on_retry_failure is not None:
                on_retry_failure(list(_exception_parsed_output(exc)))
            if retry_plan.reset_tool_registry:
                record_runtime_event("mcp_operation", "reset_tool_registry")
                try:
                    reset_tool_registry()
                except McpServerError:
                    # Callback failures keep their typed identity.
                    raise
                except Exception as mcp_exc:
                    raise _mcp_operation_error(exc, mcp_exc) from mcp_exc
            current_session_id = retry_plan.session_id
            retries_used += 1


def _iter_with_direct_mcp_recovery(  # noqa: PLR0912  # recovery loop; branches are intentional
    run_attempt: Callable[[str | None], Iterable[str]],
    *,
    max_retries: int,
    reset_tool_registry: Callable[[], object] | None = None,
    on_retry_failure: Callable[[list[str]], object] | None = None,
    on_session_observed: Callable[[str], object] | None = None,
) -> Iterator[str]:
    current_session_id: str | None = None
    retries_used = 0
    # One-reprompt bound for ``AgyIncompleteExitError`` — same invariant
    # as ``run_with_direct_mcp_recovery`` above.
    agy_incomplete_exit_reprompted = False
    while True:
        attempt_lines: deque[str] = deque(maxlen=_MAX_RECOVERY_ATTEMPT_LINES)
        try:
            for line in run_attempt(current_session_id):
                attempt_lines.append(line)
                # Use the visible-TUI-aware extractor so a session id
                # carried in a PTY banner line (with ANSI codes) is
                # captured into ``current_session_id`` and threaded
                # into the recovery plan. The plain
                # ``extract_transport_session_id_from_line`` would
                # miss the id because the anchored text patterns in
                # ``_session._TRANSPORT_TEXT_SESSION_PATTERNS`` do not
                # match TUI lines that contain ANSI escape codes.
                observed_session_id = extract_transport_session_id_with_visible_tui(line)
                if observed_session_id is not None:
                    current_session_id = observed_session_id
                    if on_session_observed is not None:
                        on_session_observed(observed_session_id)
                yield line
            return
        except McpServerError:
            raise
        except Exception as exc:
            if type(exc).__name__ == "OpenCodeResumableExitError":
                raise
            if type(exc).__name__ == "AgyIncompleteExitError":
                if agy_incomplete_exit_reprompted:
                    raise _invocation_error_with_output(exc, attempt_lines) from exc
                agy_incomplete_exit_reprompted = True
            exc_with_output = _invocation_error_with_output(exc, attempt_lines)
            if not _invocation_retry_allowed_or_raise(
                exc_with_output,
                reset_tool_registry=reset_tool_registry,
                retries_used=retries_used,
                max_retries=max_retries,
            ):
                raise exc_with_output from exc
            if reset_tool_registry is None:
                raise RuntimeError("retry registry unexpectedly unavailable") from exc
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
                record_runtime_event("mcp_operation", "reset_tool_registry")
                try:
                    reset_tool_registry()
                except McpServerError:
                    # Callback failures keep their typed identity.
                    raise
                except Exception as mcp_exc:
                    raise _mcp_operation_error(exc, mcp_exc) from mcp_exc
            current_session_id = retry_plan.session_id
            retries_used += 1


def run_with_direct_mcp_recovery[T](
    run_attempt: Callable[[str | None, Callable[[str], None]], T],
    *,
    max_retries: int,
    reset_tool_registry: Callable[[], object] | None = None,
    on_retry_failure: Callable[[list[str]], object] | None = None,
    on_session_observed: Callable[[str], object] | None = None,
    retry_resumable_exit: bool = False,
) -> T:
    """Run one recovery loop with invocation-local runtime attribution.

    ``max_retries`` (normally ``general.max_same_agent_retries``) bounds the
    automatic retries inside this invocation only. It does not decide agent
    health, demote an agent, or select the next agent.
    """
    with runtime_event_scope(RuntimeEventRecorder()):
        return _run_with_direct_mcp_recovery(
            run_attempt,
            max_retries=max_retries,
            reset_tool_registry=reset_tool_registry,
            on_retry_failure=on_retry_failure,
            on_session_observed=on_session_observed,
            retry_resumable_exit=retry_resumable_exit,
        )


def iter_with_direct_mcp_recovery(
    run_attempt: Callable[[str | None], Iterable[str]],
    *,
    max_retries: int,
    reset_tool_registry: Callable[[], object] | None = None,
    on_retry_failure: Callable[[list[str]], object] | None = None,
    on_session_observed: Callable[[str], object] | None = None,
) -> Iterator[str]:
    """Yield one recovery loop with invocation-local runtime attribution.

    ``max_retries`` (normally ``general.max_same_agent_retries``) bounds the
    automatic retries inside this invocation only. It does not decide agent
    health, demote an agent, or select the next agent.
    """
    with runtime_event_scope(RuntimeEventRecorder()):
        yield from _iter_with_direct_mcp_recovery(
            run_attempt,
            max_retries=max_retries,
            reset_tool_registry=reset_tool_registry,
            on_retry_failure=on_retry_failure,
            on_session_observed=on_session_observed,
        )


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
    if type(exc).__name__ == "AgyIncompleteExitError":
        # Preserve the typed error: the one-reprompt bound and the
        # failure classifier's typed-cause branch key on the class
        # name, so rebuilding a plain AgentInvocationError here would
        # silently disable the bounded AGY reprompt. Enrich in place
        # (parsed_output is a mutable list, same as
        # ``effect_executor._enrich_invocation_error``).
        exc.parsed_output = merged_lines
        return exc
    if merged_lines:
        return AgentInvocationError(
            exc.agent_name,
            exc.returncode,
            exc.stderr,
            parsed_output=merged_lines,
            failure_origin=exc.failure_origin,
            issuer=exc.issuer,
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
