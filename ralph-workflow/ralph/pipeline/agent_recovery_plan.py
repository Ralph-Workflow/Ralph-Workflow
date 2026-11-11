
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.agent_retry_intent import AgentRetryAction


@dataclass(frozen=True)
class AgentRecoveryPlan:

    prompt_file: str
    session_id: str | None
    reason: str
    recovery_action: AgentRetryAction | None = None
    stale_session_id: str | None = None
    transport: str | None = None
    model: str | None = None
