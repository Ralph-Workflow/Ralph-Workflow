from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._errors import AgentInvocationError
from ralph.agents.invoke._fire_reason_issuer import (
    fire_reason_from_value,
    termination_issuer_for_fire_reason,
)
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord

__all__ = (
    "TerminationRecorder",
    "_TerminationRecorder",
    "attach_attempt_facts",
    "attempt_facts_for_failure",
    "attempt_facts_from_exception",
    "observed_returncode",
    "termination_issuer_for_exception",
    "termination_issuer_for_teardown_issuer",
)

_MAX_OBSERVED_FACT_ENTRIES = 256

_OPERATOR_TEARDOWN_ISSUERS: frozenset[str] = frozenset(
    {
        "interactive_completion",
        "invoke:process",
        "invoke:pty",
        "managed_process.terminate",
        "operator_cancellation",
    }
)

_COMPLETION_ISSUER_PREFIX = "invoke:completion:"

_SHELL_SIGNAL_EXIT_MIN = 129
_SHELL_SIGNAL_EXIT_MAX = 159

_EXCEPTION_CHAIN_LIMIT = 8


@runtime_checkable
class _IssuerCarrier(Protocol):
    issuer: object


@runtime_checkable
class _ReasonCarrier(Protocol):
    reason: object


@runtime_checkable
class _ReturncodeCarrier(Protocol):
    returncode: object


@runtime_checkable
class _ExitStatusCarrier(Protocol):
    failure_origin: object


@runtime_checkable
class _PollCarrier(Protocol):
    def poll(self) -> object: ...


@runtime_checkable
class _AttemptFactsCarrier(Protocol):
    attempt_facts: object


def _return_code_may_be_a_signal(returncode: int | None) -> bool:
    if returncode is None:
        return True
    return returncode < 0 or _SHELL_SIGNAL_EXIT_MIN <= returncode <= _SHELL_SIGNAL_EXIT_MAX


def termination_issuer_for_teardown_issuer(issuer: str | None) -> TerminationIssuer:
    label = (issuer or "").strip()
    if not label:
        return TerminationIssuer.UNKNOWN
    if label == "quota_exhausted":
        return TerminationIssuer.QUOTA_DETECTOR
    with contextlib.suppress(ValueError):
        return TerminationIssuer(label)
    if label in _OPERATOR_TEARDOWN_ISSUERS or label.startswith(_COMPLETION_ISSUER_PREFIX):
        return TerminationIssuer.OPERATOR
    if fire_reason_from_value(label) is not None:
        return termination_issuer_for_fire_reason(label)
    return TerminationIssuer.UNKNOWN


def _exception_chain(exc: BaseException) -> tuple[BaseException, ...]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and len(chain) < _EXCEPTION_CHAIN_LIMIT:
        chain.append(current)
        cause = current.__cause__
        current = cause if isinstance(cause, BaseException) else None
    return tuple(chain)


def _exception_issuer_label(exc: BaseException) -> str | None:
    for candidate in _exception_chain(exc):
        label = candidate.issuer if isinstance(candidate, _IssuerCarrier) else None
        if isinstance(label, str) and label:
            return label
    return None


def _exception_fire_reason(exc: BaseException) -> WatchdogFireReason | str | None:
    for candidate in _exception_chain(exc):
        reason = candidate.reason if isinstance(candidate, _ReasonCarrier) else None
        if isinstance(reason, WatchdogFireReason):
            return reason
        if isinstance(reason, str) and reason:
            return reason
    return None


def _exception_returncode(exc: BaseException) -> int | None:
    returncode = exc.returncode if isinstance(exc, _ReturncodeCarrier) else None
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        return None
    return returncode


def _exception_exit_status(exc: BaseException) -> str | None:
    for candidate in _exception_chain(exc):
        status = candidate.failure_origin if isinstance(candidate, _ExitStatusCarrier) else None
        if isinstance(status, str) and status:
            return status
    return None


def termination_issuer_for_exception(exc: BaseException) -> TerminationIssuer:
    label = _exception_issuer_label(exc)
    if label is not None:
        return termination_issuer_for_teardown_issuer(label)
    reason = _exception_fire_reason(exc)
    if reason is not None:
        return termination_issuer_for_fire_reason(reason)
    if _return_code_may_be_a_signal(_exception_returncode(exc)):
        return TerminationIssuer.UNKNOWN
    return TerminationIssuer.AGENT_EXIT


def observed_returncode(handle: object) -> int | None:
    if not isinstance(handle, _PollCarrier):
        return None
    try:
        returncode = handle.poll()
    except Exception:
        return None
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        return None
    return returncode


@dataclass
class _TerminationRecorder:
    _issuer: TerminationIssuer | None = None
    _observed_returncode: int | None = None
    _reported_returncode: int | None = None
    _liveness_observed: bool | None = None
    _subagent_activity_observed: bool | None = None
    _elapsed_to_terminal_ms: int | None = None
    _exit_status: str | None = None
    _event_kinds: list[str] = field(default_factory=list)
    _tool_calls_completed_by_later_event: list[str] = field(default_factory=list)
    _tool_call_awaiting_later_event: str | None = None

    def _complete_tool_call_proven_by_later_event(self) -> None:
        pending = self._tool_call_awaiting_later_event
        if pending is None:
            return
        if len(self._tool_calls_completed_by_later_event) < _MAX_OBSERVED_FACT_ENTRIES:
            self._tool_calls_completed_by_later_event.append(pending)
        self._tool_call_awaiting_later_event = None

    def observe_event(self, kind: str, tool_name: str | None = None) -> None:
        self._complete_tool_call_proven_by_later_event()
        is_new_kind = not self._event_kinds or self._event_kinds[-1] != kind
        if is_new_kind and len(self._event_kinds) < _MAX_OBSERVED_FACT_ENTRIES:
            self._event_kinds.append(kind)
        if tool_name is not None:
            self._tool_call_awaiting_later_event = tool_name

    def record_issuer(self, issuer: TerminationIssuer) -> None:
        if self._issuer is None:
            self._issuer = issuer

    def record_observed_returncode(self, returncode: int | None) -> None:
        if returncode is not None:
            self._observed_returncode = returncode

    def record_reported_returncode(self, returncode: int | None) -> None:
        if returncode is not None:
            self._reported_returncode = returncode

    def record_post_exit_watchdog_fire(self) -> None:
        self._issuer = TerminationIssuer.POST_EXIT_WATCHDOG
        self._liveness_observed = True

    def record_liveness(self, observed: bool | None) -> None:
        if observed is not None and self._liveness_observed is None:
            self._liveness_observed = observed

    def record_subagent_activity(self, observed: bool | None) -> None:
        if observed is not None and self._subagent_activity_observed is None:
            self._subagent_activity_observed = observed

    def record_elapsed_to_terminal(self, milliseconds: int | None) -> None:
        if milliseconds is not None:
            self._elapsed_to_terminal_ms = milliseconds

    def record_exit_status(self, status: str | None) -> None:
        if status is not None:
            self._exit_status = status

    def termination(self) -> TerminationRecord:
        return TerminationRecord.create(
            self._issuer or TerminationIssuer.UNKNOWN,
            observed_returncode=self._observed_returncode,
            reported_returncode=self._reported_returncode,
            liveness_observed=self._liveness_observed,
        )

    def facts(self) -> AttemptFacts:
        return AttemptFacts(
            event_kinds=tuple(self._event_kinds),
            tool_calls=tuple(self._tool_calls_completed_by_later_event),
            liveness_observed=self._liveness_observed,
            subagent_activity_observed=self._subagent_activity_observed,
            elapsed_to_terminal_ms=self._elapsed_to_terminal_ms,
            exit_status=self._exit_status,
            termination=self.termination(),
        )


def attach_attempt_facts(exc: BaseException, recorder: _TerminationRecorder) -> None:
    recorder.record_reported_returncode(_exception_returncode(exc))
    facts = recorder.facts()
    object.__setattr__(exc, "attempt_facts", facts)
    if isinstance(exc, AgentInvocationError) and exc.issuer is None and facts.termination:
        exc.issuer = facts.termination.issuer.value


def attempt_facts_from_exception(exc: BaseException) -> AttemptFacts | None:
    facts = exc.attempt_facts if isinstance(exc, _AttemptFactsCarrier) else None
    return facts if isinstance(facts, AttemptFacts) else None


def attempt_facts_for_failure(exc: BaseException) -> AttemptFacts:
    attached = attempt_facts_from_exception(exc)
    if attached is not None:
        return attached
    return AttemptFacts(
        exit_status=_exception_exit_status(exc),
        termination=TerminationRecord.create(
            termination_issuer_for_exception(exc),
            reported_returncode=_exception_returncode(exc),
        ),
    )


TerminationRecorder = _TerminationRecorder
