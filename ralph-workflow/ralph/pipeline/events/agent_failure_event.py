"""Typed carrier for one real ordinary agent-failure observation.

The single owner of agent failure decisions is
:class:`ralph.recovery.agent_failure_ledger.AgentFailureLedger`. To feed it an
honest fact, the producer of the fact must hand over an immutable,
attempt-scoped value -- never a mutable module-level or enum-level attribute
that a later attempt could inherit.

:class:`AgentFailureEvent` is that value: the immutable, attempt-scoped
observation captured at the invocation boundary AND the frozen event that
carries it to the reducer, which hands it to the ledger owner.

It stays a plain dataclass so it remains serializable wherever a retry event
crosses a process or state boundary (checkpoint / JSON round-trips).
"""

from __future__ import annotations

from dataclasses import dataclass

from ralph.pipeline.events.pipeline_event import PipelineEvent


@dataclass(frozen=True, eq=False)
class AgentFailureEvent:
    """Immutable observation of ONE failed agent attempt.

    The same object is the attempt-scoped payload captured at the invocation
    boundary and the event that carries it to the reducer.

    Attributes:
        phase: Phase the attempt ran in. Empty when the producer could not
            establish the phase identity (the observation then fails closed).
        agent: Agent name the attempt ran as. Empty when the producer could
            not establish the agent identity (the observation then fails
            closed).
        artifact_valid: Whether the attempt produced a valid artifact. A
            real ordinary failure is never artifact-valid.
        workspace_changed: ``True`` / ``False`` when the workspace change was
            OBSERVED for this attempt; ``None`` when no observation was
            captured at all. ``None`` is deliberately distinct from ``False``:
            a missing observation must never be confused with an observed
            "nothing changed", and it must never inherit a previous
            attempt's fact.
        reason: Human-readable cause of the failure.
    """

    phase: str
    agent: str
    artifact_valid: bool = False
    workspace_changed: bool | None = None
    reason: str = ""

    @property
    def value(self) -> str:
        """The event kind, matching the ``PipelineEvent`` enum vocabulary."""
        return str(PipelineEvent.AGENT_FAILURE.value)

    @property
    def has_identity(self) -> bool:
        """Return whether both the phase and the agent identity are known."""
        return bool(self.phase.strip()) and bool(self.agent.strip())

    def resolved_workspace_changed(self) -> bool:
        """Resolve the workspace-change fact, failing closed.

        The workspace-change fact is the only observation that grants the
        agent extra demotion protection (the ledger resets its consecutive
        counter when the workspace changed). It is therefore granted ONLY on
        positive evidence:

        * no observation at all -> ``False``;
        * an observation whose phase/agent identity is unknown -> ``False``;
        * an observation that never measured the workspace
          (``workspace_changed is None``) -> ``False``.

        A missing identity or a missing measurement never invents an
        identity and never clears recorded history; it simply withholds the
        protection that was not earned.
        """
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
            )
        if isinstance(other, PipelineEvent):
            return other == PipelineEvent.AGENT_FAILURE
        return NotImplemented

    def __hash__(self) -> int:
        return hash(
            (self.phase, self.agent, self.artifact_valid, self.workspace_changed, self.reason)
        )


def observation_from_event(event: object) -> AgentFailureEvent | None:
    """Return the attempt-scoped observation carried by ``event``, if any.

    Returns ``None`` for every event that does not carry one (including the
    bare :data:`ralph.pipeline.events.PipelineEvent.AGENT_FAILURE` enum
    member), which the callers treat as "no observation" and therefore fail
    closed.
    """
    if isinstance(event, AgentFailureEvent):
        return event
    return None


__all__ = [
    "AgentFailureEvent",
    "observation_from_event",
]
