"""Transport-aware execution state model for agent lifecycle management.

Provides AgentExecutionState (active/waiting/resumable/terminal),
the execution strategies, and OpenCode registry routing helpers.
"""

from ._base import BaseExecutionStrategy, with_prompt_echo_flag
from ._completion_mixin import CompletionEnforcingStrategy
from ._factory import (
    _STRATEGY_DISPATCH as STRATEGY_DISPATCH,
)
from ._factory import (
    _make_agy_strategy as make_agy_strategy,
)
from ._factory import (
    _make_cursor_strategy as make_cursor_strategy,
)
from ._factory import (
    _make_kimi_strategy as make_kimi_strategy,
)
from ._factory import (
    _make_pi_strategy as make_pi_strategy,
)
from ._factory import (
    strategy_for_command,
    strategy_for_transport,
)
from ._harness_echo import is_prompt_echo_line, is_user_prompt_event_line
from ._helpers import (
    _classify_generic_child_signal as classify_generic_child_signal,
)
from ._helpers import (
    _route_opencode_line_to_registry,
)
from ._live_descendant_handle import LiveDescendantHandle
from .agent_execution_state import AgentExecutionState
from .claude_execution_strategy import ClaudeExecutionStrategy
from .claude_interactive_execution_strategy import ClaudeInteractiveExecutionStrategy
from .generic_execution_strategy import GenericExecutionStrategy
from .opencode_execution_strategy import OpenCodeExecutionStrategy

__all__ = [
    "STRATEGY_DISPATCH",
    "AgentExecutionState",
    "BaseExecutionStrategy",
    "ClaudeExecutionStrategy",
    "ClaudeInteractiveExecutionStrategy",
    "CompletionEnforcingStrategy",
    "GenericExecutionStrategy",
    "LiveDescendantHandle",
    "OpenCodeExecutionStrategy",
    "_route_opencode_line_to_registry",
    "classify_generic_child_signal",
    "is_prompt_echo_line",
    "is_user_prompt_event_line",
    "make_agy_strategy",
    "make_cursor_strategy",
    "make_kimi_strategy",
    "make_pi_strategy",
    "strategy_for_command",
    "strategy_for_transport",
    "with_prompt_echo_flag",
]
