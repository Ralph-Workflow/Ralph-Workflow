"""A single frozen dataclass threading the dependency-injection seams.

Replaces the loose ``**kwargs`` trap in BaseExecutionStrategy and the
duplicated ctx-shape in _ProcessLineReader / PtyLineReader.
"""

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
    """Frozen dataclass bundling DI seams for the executor + watchdog + strategy stack.

    Attributes:
        clock: The clock used for all timeout decisions.
        liveness_registry: Child liveness registry for tracking subprocesses.
        label_scope: Optional label scope for scoped child tracking.
        subagent_activity_sink: Optional callable invoked on subagent activity signals.
        agent_support: Optional AgentSupport for the current agent. Set after
            the per-run AgentSupport is selected; None during the very first
            frame of invoke_agent.
    """

    clock: Clock
    liveness_registry: ChildLivenessRegistry
    label_scope: str | None = None
    subagent_activity_sink: Callable[[str], None] | None = None
    agent_support: AgentSupport | None = None

    def with_support(self, support: AgentSupport) -> InvocationContext:
        """Return a new context with the support field replaced.

        All other fields (clock, liveness_registry, label_scope,
        subagent_activity_sink) are preserved from the original context.
        """
        return replace(self, agent_support=support)
