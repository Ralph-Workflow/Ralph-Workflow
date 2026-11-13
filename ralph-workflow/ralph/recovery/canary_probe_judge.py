from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from ralph.agents.agent_activity_kind import AgentActivityKind

if TYPE_CHECKING:
    from ralph.recovery.attempt_facts import AttemptFacts

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


class CanaryMarkerState(StrEnum):
    MISSING = "marker_missing"
    MISMATCHED = "marker_content_mismatch"
    MATCHED = "marker_written"


ProbeJudgeOutcome = _ProbeJudgeOutcome


@dataclass(frozen=True)
class ProbeJudgeVerdict:
    outcome: ProbeJudgeOutcome
    detail: str
    marker_written: bool | None = None


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
    if facts is not None and _agent_ran(facts):
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
