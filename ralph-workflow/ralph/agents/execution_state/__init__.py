"""Transport-aware execution state model for agent lifecycle management.

Provides AgentExecutionState (active/waiting/resumable/terminal),
the execution strategies, and OpenCode registry routing helpers.
"""

from ._base import BaseExecutionStrategy
from ._completion_mixin import CompletionEnforcingStrategy
from ._factory import strategy_for_command, strategy_for_transport
from ._helpers import _route_opencode_line_to_registry
from ._live_descendant_handle import _LiveDescendantHandle
from .agent_execution_state import AgentExecutionState
from .agy_execution_strategy import AgyExecutionStrategy
from .claude_execution_strategy import ClaudeExecutionStrategy
from .claude_interactive_execution_strategy import ClaudeInteractiveExecutionStrategy
from .generic_execution_strategy import GenericExecutionStrategy
from .opencode_execution_strategy import OpenCodeExecutionStrategy

__all__ = [
    "AgentExecutionState",
    "AgyExecutionStrategy",
    "BaseExecutionStrategy",
    "ClaudeExecutionStrategy",
    "ClaudeInteractiveExecutionStrategy",
    "CompletionEnforcingStrategy",
    "GenericExecutionStrategy",
    "OpenCodeExecutionStrategy",
    "_LiveDescendantHandle",
    "_route_opencode_line_to_registry",
    "strategy_for_command",
    "strategy_for_transport",
]
