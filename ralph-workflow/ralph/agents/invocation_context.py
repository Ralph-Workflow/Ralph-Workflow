
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.support import AgentSupport

if TYPE_CHECKING:
    from ralph.agents.clock import Clock
    from ralph.process.child_liveness import ChildLivenessRegistry


@dataclass(frozen=True, slots=True)
class InvocationContext:

    clock: Clock
    liveness_registry: ChildLivenessRegistry
    label_scope: str | None = None
    subagent_activity_sink: Callable[[str], None] | None = None
    agent_support: AgentSupport | None = None

    def with_support(self, support: AgentSupport) -> InvocationContext:
        return replace(self, agent_support=support)
