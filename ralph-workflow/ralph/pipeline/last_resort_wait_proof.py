from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.last_resort_agent_proof import LastResortAgentProof

__all__ = ["LastResortWaitProof"]


@dataclass(frozen=True, slots=True)
class LastResortWaitProof:
    continuously_unavailable_since_epoch_ms: int | None
    eligible_after_epoch_ms: int
    next_eligible_agent: str | None
    next_eligible_at_ms: int | None
    agents: tuple[LastResortAgentProof, ...]
    blocking_signature: str
    authorized: bool

    @property
    def waiting(self) -> bool:
        return not self.authorized and all(agent.tried for agent in self.agents)
