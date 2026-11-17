from __future__ import annotations

import contextlib
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

from ralph.agents.invoke._fire_reason_issuer import (
    fire_reason_from_value,
    termination_issuer_for_fire_reason,
)
from ralph.agents.watchdog_fire_reason import WatchdogFireReason
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.termination_log_entry import TerminationLogEntry

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = (
    "TerminationRecorder",
    "observed_returncode",
    "return_code_may_be_a_signal",
    "termination_issuer_for_exception",
    "termination_issuer_for_teardown_issuer",
)

_MAX_OBSERVED_FACT_ENTRIES: Final[int] = 256
_SHELL_SIGNAL_EXIT_MIN: Final[int] = 129
_SHELL_SIGNAL_EXIT_MAX: Final[int] = 159
_COMPLETION_ISSUER_PREFIX: Final[str] = "invoke:completion:"

_OPERATOR_TEARDOWN_ISSUERS: Final[frozenset[str]] = frozenset(
    {
        "interactive_completion",
        "invoke:process",
        "invoke:pty",
        "managed_process.terminate",
        "operator_cancellation",
    }
)

_TEARDOWN_ISSUER_MAP: Final[Mapping[str, TerminationIssuer]] = MappingProxyType(
    {
        "broken_agent_timer": TerminationIssuer.SUPERVISOR_IDLE,
        "call_timeout": TerminationIssuer.OPERATOR,
        "context_exit": TerminationIssuer.OPERATOR,
        "invoke_teardown": TerminationIssuer.OPERATOR,
        "output_limit": TerminationIssuer.OPERATOR,
        "quota_exhausted": TerminationIssuer.QUOTA_DETECTOR,
    }
)

_RUNTIME_TERMINATION_ISSUERS: Final[frozenset[TerminationIssuer]] = frozenset(
    {
        TerminationIssuer.SUPERVISOR_IDLE,
        TerminationIssuer.QUOTA_DETECTOR,
        TerminationIssuer.POST_EXIT_WATCHDOG,
        TerminationIssuer.RECOVERY_TIMEOUT,
        TerminationIssuer.OPERATOR,
    }
)


def termination_issuer_for_teardown_issuer(issuer: str | None) -> TerminationIssuer:
    label = (issuer or "").strip()
    if not label:
        return TerminationIssuer.UNKNOWN
    mapped = _TEARDOWN_ISSUER_MAP.get(label)
    if mapped is not None:
        return mapped
    with contextlib.suppress(ValueError):
        return TerminationIssuer(label)
    if label in _OPERATOR_TEARDOWN_ISSUERS or label.startswith(_COMPLETION_ISSUER_PREFIX):
        return TerminationIssuer.OPERATOR
    if fire_reason_from_value(label) is not None:
        return termination_issuer_for_fire_reason(label)
    return TerminationIssuer.UNKNOWN


def _exception_issuer_label(exc: BaseException) -> str | None:
    label: object = getattr(exc, "issuer", None)
    return label if isinstance(label, str) and label else None


def _exception_fire_reason(exc: BaseException) -> WatchdogFireReason | str | None:
    reason: object = getattr(exc, "reason", None)
    if isinstance(reason, WatchdogFireReason):
        return reason
    return reason if isinstance(reason, str) and reason else None


def _exception_returncode(exc: BaseException) -> int | None:
    returncode: object = getattr(exc, "returncode", None)
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        return None
    return returncode


def return_code_may_be_a_signal(returncode: int | None) -> bool:
    if returncode is None:
        return True
    return returncode < 0 or _SHELL_SIGNAL_EXIT_MIN <= returncode <= _SHELL_SIGNAL_EXIT_MAX


def termination_issuer_for_exception(exc: BaseException) -> TerminationIssuer:
    label = _exception_issuer_label(exc)
    if label is not None:
        return termination_issuer_for_teardown_issuer(label)
    reason = _exception_fire_reason(exc)
    if reason is not None:
        return termination_issuer_for_fire_reason(reason)
    returncode = _exception_returncode(exc)
    if return_code_may_be_a_signal(returncode):
        return TerminationIssuer.UNKNOWN
    return TerminationIssuer.AGENT_EXIT


@runtime_checkable
class _PollHandle(Protocol):
    def poll(self) -> int | None: ...


def observed_returncode(handle: object) -> int | None:
    if not isinstance(handle, _PollHandle):
        return None
    try:
        returncode = handle.poll()
    except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
        return None
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        return None
    return returncode


class TerminationRecorder:
    __slots__ = (
        "_elapsed_to_terminal_ms",
        "_event_kinds",
        "_exit_status",
        "_liveness_observed",
        "_observed_returncode",
        "_pending_tool",
        "_reported_returncode",
        "_subagent_activity_observed",
        "_termination_log",
        "_tool_calls",
    )

    def __init__(self) -> None:
        self._observed_returncode: int | None = None
        self._reported_returncode: int | None = None
        self._liveness_observed: bool | None = None
        self._subagent_activity_observed: bool | None = None
        self._elapsed_to_terminal_ms: int | None = None
        self._exit_status: str | None = None
        self._event_kinds: list[str] = []
        self._tool_calls: list[str] = []
        self._pending_tool: str | None = None
        self._termination_log: list[TerminationLogEntry] = []

    def observe_event(self, kind: str, tool_name: str | None = None) -> None:
        if self._pending_tool is not None:
            if len(self._tool_calls) < _MAX_OBSERVED_FACT_ENTRIES:
                self._tool_calls.append(self._pending_tool)
            self._pending_tool = None
        is_new_kind = not self._event_kinds or self._event_kinds[-1] != kind
        if is_new_kind and len(self._event_kinds) < _MAX_OBSERVED_FACT_ENTRIES:
            self._event_kinds.append(kind)
        if tool_name is not None:
            self._pending_tool = tool_name

    def record_issuer(self, issuer: TerminationIssuer, source: str | None = None) -> None:
        self._termination_log.append(
            TerminationLogEntry(issuer=issuer, source=source or issuer.value)
        )

    def record_observed_returncode(self, returncode: int | None) -> None:
        if returncode is not None:
            self._observed_returncode = returncode

    def record_reported_returncode(self, returncode: int | None) -> None:
        if returncode is not None:
            self._reported_returncode = returncode

    def record_post_exit_watchdog_fire(self) -> None:
        self.record_issuer(TerminationIssuer.POST_EXIT_WATCHDOG)
        self._liveness_observed = True

    def record_liveness(self, *, observed: bool | None) -> None:
        if observed is not None and self._liveness_observed is None:
            self._liveness_observed = observed

    def record_subagent_activity(self, *, observed: bool | None) -> None:
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
            self.resolved_issuer(),
            observed_returncode=self._observed_returncode,
            reported_returncode=self._reported_returncode,
            liveness_observed=self._liveness_observed,
            log=tuple(self._termination_log),
        )

    def resolved_issuer(self) -> TerminationIssuer:
        for entry in self._termination_log:
            if entry.issuer in _RUNTIME_TERMINATION_ISSUERS:
                return entry.issuer
        for entry in self._termination_log:
            if entry.issuer is TerminationIssuer.AGENT_EXIT:
                return TerminationIssuer.AGENT_EXIT
        return TerminationIssuer.UNKNOWN

    def facts(self) -> AttemptFacts:
        return AttemptFacts(
            event_kinds=tuple(self._event_kinds),
            tool_calls=tuple(self._tool_calls),
            liveness_observed=self._liveness_observed,
            subagent_activity_observed=self._subagent_activity_observed,
            elapsed_to_terminal_ms=self._elapsed_to_terminal_ms,
            exit_status=self._exit_status,
            termination=self.termination(),
        )
