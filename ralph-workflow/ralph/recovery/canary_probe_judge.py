from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from ralph.agents.agent_activity_kind import AgentActivityKind
from ralph.recovery.attempt_facts import TerminationIssuer
from ralph.recovery.canary_marker_state import CanaryMarkerState

if TYPE_CHECKING:
    from ralph.recovery.attempt_facts import AttemptFacts, TerminationRecord

__all__ = [
    "AGENT_RAN_EVIDENCE_KINDS",
    "CANONICAL_DETAIL_VALUES",
    "CanaryMarkerState",
    "ProbeJudgeOutcome",
    "ProbeJudgeVerdict",
    "judge_probe",
]

_REQUIRED_PROBE_JUDGE_OUTCOME_VALUES: Final[tuple[str, ...]] = (
    "passed",
    "failed_reproducing_no_work",
    "inconclusive",
)

_PROBE_COULD_NOT_RUN: Final[str] = "probe_could_not_run"
_RUNTIME_TERMINATION_REASON_PREFIX: Final[str] = "probe_terminated_by_runtime:"

AGENT_RAN_EVIDENCE_KINDS: Final[frozenset[str]] = frozenset(
    kind.value
    for kind in (
        AgentActivityKind.TOOL_USE,
        AgentActivityKind.TOOL_RESULT,
        AgentActivityKind.CHILD_PROCESS,
        AgentActivityKind.CHILD_HEARTBEAT,
        AgentActivityKind.CHILD_PROGRESS,
        AgentActivityKind.CHILD_TERMINAL_ACK,
    )
)

CANONICAL_DETAIL_VALUES: Final[frozenset[str]] = frozenset(
    {
        "marker_written",
        "marker_missing",
        "marker_content_mismatch",
        "marker_unreadable",
        _PROBE_COULD_NOT_RUN,
    }
)


class _ProbeJudgeOutcome(StrEnum):
    PASSED = "passed"
    FAILED_REPRODUCING_NO_WORK = "failed_reproducing_no_work"
    INCONCLUSIVE = "inconclusive"


if tuple(outcome.value for outcome in _ProbeJudgeOutcome) != _REQUIRED_PROBE_JUDGE_OUTCOME_VALUES:
    raise RuntimeError(
        "ProbeJudgeOutcome must expose exactly passed, failed_reproducing_no_work, inconclusive"
    )

if frozenset({_PROBE_COULD_NOT_RUN, *(state.value for state in CanaryMarkerState)}) != (
    CANONICAL_DETAIL_VALUES
):
    raise RuntimeError(
        "CANONICAL_DETAIL_VALUES must hold exactly the canary marker states and probe_could_not_run"
    )


ProbeJudgeOutcome = _ProbeJudgeOutcome


@dataclass(frozen=True)
class ProbeJudgeVerdict:
    outcome: ProbeJudgeOutcome
    detail: str
    marker_written: bool | None = None
    reason: str | None = None


def _format_runtime_termination_reason(termination: TerminationRecord) -> str:
    rc = (
        termination.observed_returncode
        if termination.observed_returncode is not None
        else termination.reported_returncode
    )
    if rc is not None:
        return f"{_RUNTIME_TERMINATION_REASON_PREFIX}{termination.issuer.value}:rc={rc}"
    return f"{_RUNTIME_TERMINATION_REASON_PREFIX}{termination.issuer.value}"


def judge_probe(
    marker_state: CanaryMarkerState,
    facts: AttemptFacts | None,
) -> ProbeJudgeVerdict:
    if marker_state is CanaryMarkerState.MATCHED:
        return ProbeJudgeVerdict(
            outcome=ProbeJudgeOutcome.PASSED,
            detail=CanaryMarkerState.MATCHED.value,
            marker_written=True,
        )
    if marker_state is CanaryMarkerState.UNREADABLE:
        return ProbeJudgeVerdict(
            outcome=ProbeJudgeOutcome.INCONCLUSIVE,
            detail=_PROBE_COULD_NOT_RUN,
            marker_written=None,
        )
    if facts is not None and _agent_ran(facts):
        termination = facts.termination
        if termination is not None:
            if termination.issuer is not TerminationIssuer.AGENT_EXIT:
                return ProbeJudgeVerdict(
                    outcome=ProbeJudgeOutcome.INCONCLUSIVE,
                    detail=_PROBE_COULD_NOT_RUN,
                    marker_written=None,
                    reason=_format_runtime_termination_reason(termination),
                )
            if not termination.return_code_discrepancy:
                return ProbeJudgeVerdict(
                    outcome=ProbeJudgeOutcome.FAILED_REPRODUCING_NO_WORK,
                    detail=marker_state.value,
                    marker_written=False,
                )
    return ProbeJudgeVerdict(
        outcome=ProbeJudgeOutcome.INCONCLUSIVE,
        detail=_PROBE_COULD_NOT_RUN,
        marker_written=None,
    )


def _agent_ran(facts: AttemptFacts) -> bool:
    if any(kind in AGENT_RAN_EVIDENCE_KINDS for kind in facts.event_kinds):
        return True
    if facts.tool_calls:
        return True
    return facts.subagent_activity_observed is True
