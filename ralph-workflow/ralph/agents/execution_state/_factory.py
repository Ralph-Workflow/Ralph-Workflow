from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.config.enums import AgentTransport

from .claude_execution_strategy import ClaudeExecutionStrategy
from .claude_interactive_execution_strategy import ClaudeInteractiveExecutionStrategy
from .generic_execution_strategy import GenericExecutionStrategy
from .opencode_execution_strategy import OpenCodeExecutionStrategy

if TYPE_CHECKING:
    from ralph.process.child_liveness import ChildLivenessRegistry


def strategy_for_transport(
    transport: object,
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
) -> GenericExecutionStrategy | OpenCodeExecutionStrategy:
    """Return the appropriate ExecutionStrategy for an agent transport."""
    if transport == AgentTransport.OPENCODE:
        return OpenCodeExecutionStrategy(label_scope=label_scope, registry=registry)
    if transport == AgentTransport.CLAUDE:
        return ClaudeExecutionStrategy()
    if transport == AgentTransport.CLAUDE_INTERACTIVE:
        return ClaudeInteractiveExecutionStrategy()
    if transport == AgentTransport.AGY:
        return ClaudeInteractiveExecutionStrategy()
    return GenericExecutionStrategy()
