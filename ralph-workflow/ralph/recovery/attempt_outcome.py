from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind

if TYPE_CHECKING:
    from ralph.recovery.attempt_facts import TerminationIssuer
    from ralph.recovery.dead_agent_verdict import PersistedVerdict

__all__ = ("AttemptOutcome",)


@dataclass(frozen=True)
class _AttemptOutcome:
    kind: AttemptOutcomeKind
    issuer: TerminationIssuer | None
    verdict: PersistedVerdict | None
    reason: str

    def __post_init__(self) -> None:
        runtime_ended = self.kind is AttemptOutcomeKind.RUNTIME_ENDED
        if runtime_ended is not (self.issuer is not None):
            raise ValueError("issuer must be set if and only if the runtime ended the attempt")
        if self.kind is AttemptOutcomeKind.AGENT_DEAD and self.verdict is None:
            raise ValueError("an agent-dead outcome requires a persisted verdict")

    @property
    def benches_agent(self) -> bool:
        return self.kind is AttemptOutcomeKind.AGENT_DEAD

    @property
    def counts_toward_agent_state(self) -> bool:
        return self.kind not in (
            AttemptOutcomeKind.COMPLETED,
            AttemptOutcomeKind.RUNTIME_ENDED,
            AttemptOutcomeKind.TASK_OR_ENVIRONMENT_FAILURE,
        )

    @property
    def retries_same_agent(self) -> bool:
        return self.kind is AttemptOutcomeKind.RETRYABLE_FAILURE


AttemptOutcome = _AttemptOutcome
