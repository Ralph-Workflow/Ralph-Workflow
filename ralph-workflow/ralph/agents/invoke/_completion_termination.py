from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._errors import AgentInvocationError
from ralph.agents.invoke._fire_reason_issuer import (
    fire_reason_from_value,
    termination_issuer_for_fire_reason,
)
from ralph.checked_accessors import optional_bool, optional_int, optional_str
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord

if TYPE_CHECKING:
    from collections.abc import Callable

_ATTEMPT_FACTS_ATTRIBUTE = "attempt_facts"

_OPERATOR_TEARDOWN_ISSUERS: frozenset[str] = frozenset(
    {
        "interactive_completion",
        "invoke:process",
        "invoke:pty",
        "managed_process.terminate",
        "operator_cancellation",
    }
)

_MAX_OBSERVED_FACT_ENTRIES = 256

_COMPLETION_ISSUER_PREFIX = "invoke:completion:"

_SHELL_SIGNAL_EXIT_MIN = 129
_SHELL_SIGNAL_EXIT_MAX = 159

_EXCEPTION_CHAIN_LIMIT = 8


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
        label = cast("object", getattr(candidate, "issuer", None))
        if isinstance(label, str) and label:
            return label
    return None


def _exception_fire_reason(exc: BaseException) -> WatchdogFireReason | str | None:
    for candidate in _exception_chain(exc):
        reason = cast("object", getattr(candidate, "reason", None))
        if isinstance(reason, WatchdogFireReason):
            return reason
        if isinstance(reason, str) and reason:
            return reason
    return None


def _exception_returncode(exc: BaseException) -> int | None:
    returncode = cast("object", getattr(exc, "returncode", None))
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        return None
    return returncode


def _exception_exit_status(exc: BaseException) -> str | None:
    for candidate in _exception_chain(exc):
        status = cast("object", getattr(candidate, "failure_origin", None))
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
    poll = cast("Callable[[], int | None] | None", getattr(handle, "poll", None))
    if poll is None:
        return None
    try:
        returncode = poll()
    except Exception:
        return None
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        return None
    return returncode


def _recorded_issuer(value: object) -> TerminationIssuer | None:
    return value if isinstance(value, TerminationIssuer) else None


@dataclass
class _TerminationRecorder:
    _values: list[object] = field(default_factory=lambda: [None] * 7)
    _event_kinds: list[str] = field(default_factory=list)
    _tool_calls: list[str] = field(default_factory=list)
    _pending_tool: str | None = None

    def _complete_pending_tool(self) -> None:
        if self._pending_tool is None:
            return
        if len(self._tool_calls) < _MAX_OBSERVED_FACT_ENTRIES:
            self._tool_calls.append(self._pending_tool)
        self._pending_tool = None

    def observe_event(self, kind: str, tool_name: str | None = None) -> None:
        self._complete_pending_tool()
        is_new_kind = not self._event_kinds or self._event_kinds[-1] != kind
        if is_new_kind and len(self._event_kinds) < _MAX_OBSERVED_FACT_ENTRIES:
            self._event_kinds.append(kind)
        if tool_name is not None:
            self._pending_tool = tool_name

    def record_issuer(self, issuer: TerminationIssuer) -> None:
        if self._values[0] is None:
            self._values[0] = issuer

    def record_observed_returncode(self, returncode: int | None) -> None:
        if returncode is not None:
            self._values[1] = returncode

    def record_reported_returncode(self, returncode: int | None) -> None:
        if returncode is not None:
            self._values[2] = returncode

    def record_post_exit_watchdog_fire(self) -> None:
        self._values[0] = TerminationIssuer.POST_EXIT_WATCHDOG
        self._values[3] = True

    def record_liveness(self, observed: bool | None) -> None:
        if observed is not None and self._values[3] is None:
            self._values[3] = observed

    def record_subagent_activity(self, observed: bool | None) -> None:
        if observed is not None and self._values[4] is None:
            self._values[4] = observed

    def record_elapsed_to_terminal(self, milliseconds: int | None) -> None:
        if milliseconds is not None:
            self._values[5] = milliseconds

    def record_exit_status(self, status: str | None) -> None:
        if status is not None:
            self._values[6] = status

    def termination(self) -> TerminationRecord:
        return TerminationRecord.create(
            _recorded_issuer(self._values[0]) or TerminationIssuer.UNKNOWN,
            observed_returncode=optional_int(self._values[1]),
            reported_returncode=optional_int(self._values[2]),
            liveness_observed=optional_bool(self._values[3]),
        )

    def facts(self) -> AttemptFacts:
        return AttemptFacts(
            event_kinds=tuple(self._event_kinds),
            tool_calls=tuple(self._tool_calls),
            liveness_observed=optional_bool(self._values[3]),
            subagent_activity_observed=optional_bool(self._values[4]),
            elapsed_to_terminal_ms=optional_int(self._values[5]),
            exit_status=optional_str(self._values[6]),
            termination=self.termination(),
        )


TerminationRecorder = _TerminationRecorder


def attach_attempt_facts(exc: BaseException, recorder: _TerminationRecorder) -> None:
    recorder.record_reported_returncode(_exception_returncode(exc))
    facts = recorder.facts()
    setattr(exc, _ATTEMPT_FACTS_ATTRIBUTE, facts)
    if isinstance(exc, AgentInvocationError) and exc.issuer is None and facts.termination:
        exc.issuer = facts.termination.issuer.value


def attempt_facts_from_exception(exc: BaseException) -> AttemptFacts | None:
    facts = cast("object", getattr(exc, _ATTEMPT_FACTS_ATTRIBUTE, None))
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


__all__ = [
    "TerminationRecorder",
    "attach_attempt_facts",
    "attempt_facts_for_failure",
    "attempt_facts_from_exception",
    "observed_returncode",
    "termination_issuer_for_exception",
    "termination_issuer_for_teardown_issuer",
]
