"""Shared direct-invocation recovery for post-tool MCP continuation failures."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._agent_invocation_error import AgentInvocationError
from ralph.pipeline.agent_retry_decision import resolve_retry_intent

from ._session import (
    extract_transport_session_id,
    extract_transport_session_id_with_visible_tui,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

_MAX_RECOVERY_ATTEMPT_LINES = 400
_RETRY_FAILURE_EVIDENCE_LINES = 5
_MAX_SAME_SIGNATURE_RETRY_DELAY_SECONDS: Final[float] = 0.004
_INITIAL_SAME_SIGNATURE_RETRY_DELAY_SECONDS: Final[float] = 0.001
_REPEATED_FAILURE_SIGNATURE_THRESHOLD: Final[int] = 2


@dataclass(frozen=True)
class _DirectMcpRetryPlan:
    session_id: str | None
    reset_tool_registry: bool
    skip_same_agent_retries: bool


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


def _retry_failure_signature(exc: Exception) -> tuple[str, str, tuple[str, ...]]:
    """Return the stable evidence used to detect a repeated failed attempt."""

    return (
        type(exc).__name__,
        str(exc),
        _exception_parsed_output(exc),
    )


def _advance_failure_signature(
    previous_signature: tuple[str, str, tuple[str, ...]] | None,
    consecutive_failures: int,
    exc: Exception,
) -> tuple[tuple[str, str, tuple[str, ...]], int]:
    """Increment one stable failure signature or begin a new sequence."""

    signature = _retry_failure_signature(exc)
    if signature == previous_signature:
        return signature, consecutive_failures + 1
    return signature, 1


def _retry_cooldown_seconds(consecutive_failures: int) -> float:
    """Return a bounded exponential delay only for repeated failures."""

    if consecutive_failures < _REPEATED_FAILURE_SIGNATURE_THRESHOLD:
        return 0.0
    delay = _INITIAL_SAME_SIGNATURE_RETRY_DELAY_SECONDS * math.pow(
        2.0, consecutive_failures - _REPEATED_FAILURE_SIGNATURE_THRESHOLD
    )
    return min(delay, _MAX_SAME_SIGNATURE_RETRY_DELAY_SECONDS)


def _retry_allowed_or_raise(
    exc: Exception,
    *,
    reset_tool_registry: Callable[[], object] | None,
    retries_used: int,
    max_retries: int,
    consecutive_failures: int,
) -> bool:
    """Return whether another retry is allowed, retaining terminal evidence."""

    if reset_tool_registry is not None and retries_used < max_retries:
        return True
    if consecutive_failures >= _REPEATED_FAILURE_SIGNATURE_THRESHOLD:
        raise _terminal_retry_error(exc, consecutive_failures) from exc
    return False


def _apply_retry_cooldown(
    consecutive_failures: int, sleep_fn: Callable[[float], object]
) -> None:
    cooldown_seconds = _retry_cooldown_seconds(consecutive_failures)
    if cooldown_seconds:
        sleep_fn(cooldown_seconds)


def _terminal_retry_error(exc: Exception, consecutive_failures: int) -> Exception:
    """Preserve the failure evidence while making same-signature exhaustion explicit."""

    diagnostic = (
        "Commit/direct-MCP retry cooldown exhausted after "
        f"{consecutive_failures} identical failure signatures; try the next eligible agent."
    )
    if isinstance(exc, AgentInvocationError):
        return AgentInvocationError(
            exc.agent_name,
            exc.returncode,
            exc.stderr,
            parsed_output=[*_exception_parsed_output(exc), diagnostic],
        )
    return RuntimeError(f"{diagnostic} Original failure: {exc}")


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


def run_with_direct_mcp_recovery[T](
    run_attempt: Callable[[str | None, Callable[[str], None]], T],
    *,
    max_retries: int,
    reset_tool_registry: Callable[[], object] | None = None,
    on_retry_failure: Callable[[list[str]], object] | None = None,
    on_session_observed: Callable[[str], object] | None = None,
    retry_resumable_exit: bool = False,
    sleep: Callable[[float], object] | None = None,
) -> T:
    current_session_id: str | None = None
    retries_used = 0
    previous_failure_signature: tuple[str, str, tuple[str, ...]] | None = None
    consecutive_failure_signatures = 0
    sleep_fn: Callable[[float], object] = time.sleep if sleep is None else sleep
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
        except Exception as exc:
            if type(exc).__name__ == "OpenCodeResumableExitError" and not retry_resumable_exit:
                raise
            if type(exc).__name__ == "AgyIncompleteExitError":
                if agy_incomplete_exit_reprompted:
                    raise
                agy_incomplete_exit_reprompted = True
            (
                previous_failure_signature,
                consecutive_failure_signatures,
            ) = _advance_failure_signature(
                previous_failure_signature, consecutive_failure_signatures, exc
            )
            if not _retry_allowed_or_raise(
                exc,
                reset_tool_registry=reset_tool_registry,
                retries_used=retries_used,
                max_retries=max_retries,
                consecutive_failures=consecutive_failure_signatures,
            ):
                raise
            _apply_retry_cooldown(consecutive_failure_signatures, sleep_fn)
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
    sleep: Callable[[float], object] | None = None,
) -> Iterator[str]:
    current_session_id: str | None = None
    retries_used = 0
    previous_failure_signature: tuple[str, str, tuple[str, ...]] | None = None
    consecutive_failure_signatures = 0
    sleep_fn: Callable[[float], object] = time.sleep if sleep is None else sleep
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
        except Exception as exc:
            if type(exc).__name__ == "OpenCodeResumableExitError":
                raise
            if type(exc).__name__ == "AgyIncompleteExitError":
                if agy_incomplete_exit_reprompted:
                    raise _invocation_error_with_output(exc, attempt_lines) from exc
                agy_incomplete_exit_reprompted = True
            exc_with_output = _invocation_error_with_output(exc, attempt_lines)
            (
                previous_failure_signature,
                consecutive_failure_signatures,
            ) = _advance_failure_signature(
                previous_failure_signature, consecutive_failure_signatures, exc_with_output
            )
            if not _retry_allowed_or_raise(
                exc_with_output,
                reset_tool_registry=reset_tool_registry,
                retries_used=retries_used,
                max_retries=max_retries,
                consecutive_failures=consecutive_failure_signatures,
            ):
                raise exc_with_output from exc
            _apply_retry_cooldown(consecutive_failure_signatures, sleep_fn)
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
