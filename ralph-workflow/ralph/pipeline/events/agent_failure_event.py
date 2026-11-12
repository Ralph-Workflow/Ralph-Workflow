from __future__ import annotations

from dataclasses import dataclass

from ralph.pipeline.events.pipeline_event import PipelineEvent
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer


@dataclass(frozen=True, eq=False)
class AgentFailureEvent:
    phase: str
    agent: str
    artifact_valid: bool = False
    workspace_changed: bool | None = None
    reason: str = ""
    already_charged: bool = False
    facts: AttemptFacts | None = None

    @property
    def value(self) -> str:
        return str(PipelineEvent.AGENT_FAILURE.value)

    @property
    def has_identity(self) -> bool:
        return bool(self.phase.strip()) and bool(self.agent.strip())

    @property
    def termination_issuer(self) -> str:
        termination = self.facts.termination if self.facts is not None else None
        if termination is None:
            return TerminationIssuer.UNKNOWN.value
        return termination.issuer.value

    def resolved_workspace_changed(self) -> bool:
        if not self.has_identity:
            return False
        return bool(self.workspace_changed)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, AgentFailureEvent):
            return (
                self.phase == other.phase
                and self.agent == other.agent
                and self.artifact_valid == other.artifact_valid
                and self.workspace_changed == other.workspace_changed
                and self.reason == other.reason
                and self.already_charged == other.already_charged
            )
        if isinstance(other, PipelineEvent):
            return other == PipelineEvent.AGENT_FAILURE
        return NotImplemented

    def __hash__(self) -> int:
        return hash(
            (
                self.phase,
                self.agent,
                self.artifact_valid,
                self.workspace_changed,
                self.reason,
                self.already_charged,
            )
        )


def observation_from_event(event: object) -> AgentFailureEvent | None:
    if isinstance(event, AgentFailureEvent):
        return event
    return None


__all__ = [
    "AgentFailureEvent",
    "observation_from_event",
]
