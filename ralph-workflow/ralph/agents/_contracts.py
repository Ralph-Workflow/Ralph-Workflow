"""Single source of truth for the agent-subsystem protocols.

The 3 pre-existing _StrategyFactory re-declarations are deprecated; new code
MUST import StrategyFactory from ralph.agents._contracts.
The 2 single-method Clock Protocol shadows in idle_watchdog/* are deprecated;
new code MUST import Clock from ralph.agents.clock (re-exported here) or from
ralph.agents.clock directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

# Re-exports from canonical homes.  New code MUST import these symbols from
# their canonical module or from this module; do NOT re-declare them locally.
from ralph.agents.clock import Clock
from ralph.agents.parsers.base import AgentParser
from ralph.agents.system_clock import SystemClock

if TYPE_CHECKING:
    from ralph.agents.execution_state._base import BaseExecutionStrategy
    from ralph.process.child_liveness import ChildLivenessRegistry


__all__ = ["AgentParser", "Clock", "StrategyFactory", "SystemClock"]


class StrategyFactory(Protocol):
    """Factory that returns a ``BaseExecutionStrategy`` with runtime kwargs."""

    def __call__(
        self,
        *,
        label_scope: str | None,
        registry: ChildLivenessRegistry | None,
    ) -> BaseExecutionStrategy: ...
