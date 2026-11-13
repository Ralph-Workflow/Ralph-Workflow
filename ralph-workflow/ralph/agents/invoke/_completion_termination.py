from __future__ import annotations

from typing import cast

from ralph.agents.invoke._errors import AgentInvocationError
from ralph.agents.invoke.termination_recorder import (
    TerminationRecorder,
    exception_exit_status,
    observed_returncode,
    termination_issuer_for_exception,
    termination_issuer_for_teardown_issuer,
)
from ralph.recovery.attempt_facts import AttemptFacts, TerminationRecord

__all__ = [
    "TerminationRecorder",
    "attach_attempt_facts",
    "attempt_facts_for_failure",
    "attempt_facts_from_exception",
    "observed_returncode",
    "termination_issuer_for_exception",
    "termination_issuer_for_teardown_issuer",
]

_ATTEMPT_FACTS_ATTRIBUTE = "attempt_facts"

_TerminationRecorder = TerminationRecorder


def _exception_returncode(exc: BaseException) -> int | None:
    returncode = cast("object", getattr(exc, "returncode", None))
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        return None
    return returncode


def attach_attempt_facts(exc: BaseException, recorder: TerminationRecorder) -> None:
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
        exit_status=exception_exit_status(exc),
        termination=TerminationRecord.create(
            termination_issuer_for_exception(exc),
            reported_returncode=_exception_returncode(exc),
        ),
    )
