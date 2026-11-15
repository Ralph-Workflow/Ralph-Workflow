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

AGENT_RAN_EVIDENCE_KINDS: Final[frozenset[str]] = frozenset(
    kind.value
    for kind in (
        AgentActivityKind.TOOL_USE,
        AgentActivityKind.TOOL_RESULT,
        AgentActivityKind.STREAM_DELTA,
        AgentActivityKind.CHILD_PROCESS,
        AgentActivityKind.CHILD_HEARTBEAT,
        AgentActivityKind.CHILD_PROGRESS,
        AgentActivityKind.CHILD_TERMINAL_ACK,
    )
)


class _ProbeJudgeOutcome(StrEnum):
    PASSED = "passed"
    FAILED_REPRODUCING_NO_WORK = "failed_reproducing_no_work"
    INCONCLUSIVE = "inconclusive"


if tuple(outcome.value for outcome in _ProbeJudgeOutcome) != _REQUIRED_PROBE_JUDGE_OUTCOME_VALUES:
    raise RuntimeError(
        "ProbeJudgeOutcome must expose exactly passed, failed_reproducing_no_work, inconclusive"
    )


ProbeJudgeOutcome = _ProbeJudgeOutcome


@dataclass(frozen=True)
class ProbeJudgeVerdict:
    outcome: ProbeJudgeOutcome
    detail: str
    marker_written: bool | None = None


def _format_runtime_termination_detail(termination: TerminationRecord) -> str:
    rc = (
        termination.observed_returncode
        if termination.observed_returncode is not None
        else termination.reported_returncode
    )
    if rc is not None:
        return f"probe_terminated_by_runtime:{termination.issuer.value}:rc={rc}"
    return f"probe_terminated_by_runtime:{termination.issuer.value}"


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
            detail="probe_could_not_run",
            marker_written=None,
        )
    if facts is not None and _agent_ran(facts):
        termination = facts.termination
        if termination is not None:
            if termination.issuer is not TerminationIssuer.AGENT_EXIT:
                return ProbeJudgeVerdict(
                    outcome=ProbeJudgeOutcome.INCONCLUSIVE,
                    detail=_format_runtime_termination_detail(termination),
                    marker_written=None,
                )
            if not termination.return_code_discrepancy:
                return ProbeJudgeVerdict(
                    outcome=ProbeJudgeOutcome.FAILED_REPRODUCING_NO_WORK,
                    detail=marker_state.value,
                    marker_written=False,
                )
    return ProbeJudgeVerdict(
        outcome=ProbeJudgeOutcome.INCONCLUSIVE,
        detail="probe_could_not_run",
        marker_written=None,
    )


def _agent_ran(facts: AttemptFacts) -> bool:
    if any(kind in AGENT_RAN_EVIDENCE_KINDS for kind in facts.event_kinds):
        return True
    if facts.tool_calls:
        return True
    return facts.subagent_activity_observed is True
