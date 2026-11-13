from __future__ import annotations

from typing import cast

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._fire_reason_issuer import termination_issuer_for_fire_reason
from ralph.agents.invoke.termination_recorder import (
    TerminationRecorder,
    observed_returncode,
    return_code_may_be_a_signal,
    termination_issuer_for_exception,
    termination_issuer_for_teardown_issuer,
)
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord

__all__ = [
    "AttemptEnded",
    "TerminationRecorder",
    "attempt_facts_for_failure",
    "observed_returncode",
    "publish_attempt_facts",
    "termination_issuer_for_exception",
    "termination_issuer_for_teardown_issuer",
]

_TerminationRecorder = TerminationRecorder

_return_code_may_be_a_signal = return_code_may_be_a_signal


def _exception_returncode(exc: BaseException) -> int | None:
    returncode = cast("object", getattr(exc, "returncode", None))
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        return None
    return returncode


def _own_issuer_label(exc: BaseException) -> str | None:
    label = cast("object", getattr(exc, "issuer", None))
    return label if isinstance(label, str) and label else None


def _own_fire_reason(exc: BaseException) -> WatchdogFireReason | str | None:
    reason = cast("object", getattr(exc, "reason", None))
    if isinstance(reason, WatchdogFireReason):
        return reason
    if isinstance(reason, str) and reason:
        return reason
    return None


def _own_exit_status(exc: BaseException) -> str | None:
    status = cast("object", getattr(exc, "failure_origin", None))
    return status if isinstance(status, str) and status else None


def _derived_attempt_facts(exc: BaseException) -> AttemptFacts:
    label = _own_issuer_label(exc)
    if label is not None:
        issuer = termination_issuer_for_teardown_issuer(label)
    else:
        reason = _own_fire_reason(exc)
        if reason is not None:
            issuer = termination_issuer_for_fire_reason(reason)
        elif return_code_may_be_a_signal(_exception_returncode(exc)):
            issuer = TerminationIssuer.UNKNOWN
        else:
            issuer = TerminationIssuer.AGENT_EXIT
    return AttemptFacts(
        exit_status=_own_exit_status(exc),
        termination=TerminationRecord.create(
            issuer,
            reported_returncode=_exception_returncode(exc),
        ),
    )


class AttemptEnded:
    def __init__(self, *args: object, facts: AttemptFacts | None = None) -> None:
        self._explicit_attempt_facts = facts
        super().__init__(*args)

    @property
    def facts(self) -> AttemptFacts:
        explicit = self._explicit_attempt_facts
        if explicit is not None:
            return explicit
        return _derived_attempt_facts(cast("BaseException", self))

    @facts.setter
    def facts(self, value: AttemptFacts) -> None:
        self._explicit_attempt_facts = value
        self._on_facts_published(value)

    def _on_facts_published(self, facts: AttemptFacts) -> None:
        del facts


def publish_attempt_facts(exc: BaseException, recorder: _TerminationRecorder) -> AttemptFacts:
    recorder.record_reported_returncode(_exception_returncode(exc))
    facts = recorder.facts()
    if isinstance(exc, AttemptEnded):
        exc.facts = facts
    return facts


def attempt_facts_for_failure(exc: BaseException) -> AttemptFacts:
    if isinstance(exc, AttemptEnded):
        return exc.facts
    return AttemptFacts()
