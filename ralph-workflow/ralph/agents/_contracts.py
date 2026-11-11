
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ralph.agents.clock import Clock
from ralph.agents.parsers.base import AgentParser
from ralph.agents.system_clock import SystemClock

if TYPE_CHECKING:
    from ralph.agents.execution_state._base import BaseExecutionStrategy
    from ralph.process.child_liveness import ChildLivenessRegistry
    from ralph.process.monitor import SubagentPidSource


__all__ = ["AgentParser", "Clock", "StrategyFactory", "SystemClock"]


class StrategyFactory(Protocol):

    def __call__(
        self,
        *,
        label_scope: str | None,
        registry: ChildLivenessRegistry | None,
        subagent_pid_source: SubagentPidSource | None = None,
    ) -> BaseExecutionStrategy: ...
