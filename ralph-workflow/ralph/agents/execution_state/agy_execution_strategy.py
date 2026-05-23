"""Execution strategy for Google Anti Gravity (AGY) agents."""

from __future__ import annotations

from .claude_interactive_execution_strategy import ClaudeInteractiveExecutionStrategy


class AgyExecutionStrategy(ClaudeInteractiveExecutionStrategy):
    """AGY strategy: no session continuation, completion evidence still required."""

    def supports_session_continuation(self) -> bool:
        return False

    def supports_completion_enforcement(self) -> bool:
        return True
