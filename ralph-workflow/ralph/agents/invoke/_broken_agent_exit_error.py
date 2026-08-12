"""Typed failure for an agent that is alive but cannot produce LLM output."""

from __future__ import annotations

from typing import Literal

from ralph.agents.invoke._agent_invocation_error import AgentInvocationError

_BrokenAgentReason = Literal["no_output", "prompt_echo", "no_llm_activity"]
_VALID_REASONS = frozenset({"no_output", "prompt_echo", "no_llm_activity"})


class BrokenAgentExitError(AgentInvocationError):
    """Signal that recovery must fall over instead of retrying this agent."""

    def __init__(
        self,
        agent_name: str,
        *,
        reason: _BrokenAgentReason,
        elapsed_seconds: float | None = None,
        grace_seconds: float | None = None,
    ) -> None:
        if reason not in _VALID_REASONS:
            msg = f"unsupported broken-agent reason: {reason!r}"
            raise ValueError(msg)
        self.skip_same_agent_retries = True
        self.reason = reason
        self.elapsed_seconds = elapsed_seconds
        self.grace_seconds = grace_seconds
        context = {
            "no_output": "no meaningful LLM output",
            "prompt_echo": "only prompt-echo output",
            "no_llm_activity": "no meaningful LLM activity",
        }[reason]
        super().__init__(
            agent_name,
            0,
            f"agent appears broken: {context}; check credentials or provider availability",
        )


__all__ = ["BrokenAgentExitError"]
