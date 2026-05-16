"""Transport-aware execution state model for agent lifecycle management.

Provides AgentExecutionState (active/waiting/resumable/terminal),
the execution strategies, and OpenCode registry routing helpers.
"""

from ._factory import strategy_for_transport
from ._helpers import _route_opencode_line_to_registry
from ._live_descendant_handle import _LiveDescendantHandle
from .agent_execution_state import AgentExecutionState
from .claude_execution_strategy import ClaudeExecutionStrategy
from .claude_interactive_execution_strategy import ClaudeInteractiveExecutionStrategy
from .generic_execution_strategy import GenericExecutionStrategy
from .opencode_execution_strategy import OpenCodeExecutionStrategy

__all__ = [
    "AgentExecutionState",
    "ClaudeExecutionStrategy",
    "ClaudeInteractiveExecutionStrategy",
    "GenericExecutionStrategy",
    "OpenCodeExecutionStrategy",
    "_LiveDescendantHandle",
    "_route_opencode_line_to_registry",
    "strategy_for_transport",
]
