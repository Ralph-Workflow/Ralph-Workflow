
from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from ralph.agents.invoke._agent_invocation_error import AgentInvocationError

if TYPE_CHECKING:
    from ralph.agents.execution_state import BaseExecutionStrategy


class AgyIncompleteExitError(AgentInvocationError):

    def __init__(self, agent_name: str, diagnostic: str | None = None) -> None:
        canonical = (
            "agent exited without required completion evidence "
            "(completion sentinel missing, or required artifact receipt missing). "
            "Ralph issues exactly one automatic recovery reprompt per invocation "
            "for this condition; seeing this error means that reprompt was "
            "already spent or did not produce the evidence. Action: inspect the "
            "agent transcript — the agent must finish the assigned work "
            "autonomously (no interactive input is available), submit any "
            "required artifact via the ralph_submit_md_artifact MCP tool, and "
            "call declare_complete before exiting."
        )
        message = f"{diagnostic}\n{canonical}" if diagnostic else canonical
        super().__init__(agent_name, 0, message)


def raise_missing_completion_evidence(
    agent_name: str,
    execution_strategy: BaseExecutionStrategy,
    diagnostic: str | None,
) -> NoReturn:
    if execution_strategy.supports_incomplete_exit_reprompt():
        raise AgyIncompleteExitError(agent_name, diagnostic=diagnostic)
    canonical = (
        "agent exited without required completion evidence "
        "(completion sentinel missing, or required artifact receipt missing)"
    )
    message = f"{diagnostic}\n{canonical}" if diagnostic else canonical
    raise AgentInvocationError(agent_name, 0, message)


__all__ = ["AgyIncompleteExitError", "raise_missing_completion_evidence"]
