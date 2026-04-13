"""Effect types: what the pipeline wants to do next.

Effects are emitted by the orchestrator and describe the next action
to be taken. They carry all necessary data for the effect handler
to execute the action.

No I/O is performed in this module - effects are pure data descriptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.config.enums import PipelinePhase


@dataclass(frozen=True)
class InvokeAgentEffect:
    """Effect to invoke an AI agent.

    Attributes:
        agent_name: Name of the agent to invoke.
        phase: Current pipeline phase.
        prompt_file: Path to the prompt file for the agent.
        chain_name: Name of the agent chain being used.
    """

    agent_name: str
    phase: PipelinePhase
    prompt_file: str
    chain_name: str = ""


@dataclass(frozen=True)
class PreparePromptEffect:
    """Effect to prepare a prompt for an agent.

    Attributes:
        phase: Current pipeline phase.
        iteration: Current iteration number.
    """

    phase: PipelinePhase
    iteration: int


@dataclass(frozen=True)
class CommitEffect:
    """Effect to create a git commit.

    Attributes:
        message_file: Path to the commit message file.
    """

    message_file: str


@dataclass(frozen=True)
class PushEffect:
    """Effect to push changes to remote.

    Attributes:
        remote: Remote name (default: origin).
        branch: Branch name (optional, uses current branch if not specified).
    """

    remote: str = "origin"
    branch: str | None = None


@dataclass(frozen=True)
class SaveCheckpointEffect:
    """Effect to save a checkpoint."""
    pass


@dataclass(frozen=True)
class ExitSuccessEffect:
    """Effect to exit with success."""
    pass


@dataclass(frozen=True)
class ExitFailureEffect:
    """Effect to exit with failure.

    Attributes:
        reason: Reason for the failure.
    """

    reason: str


# Union type for all effects
Effect = (
    InvokeAgentEffect
    | PreparePromptEffect
    | CommitEffect
    | PushEffect
    | SaveCheckpointEffect
    | ExitSuccessEffect
    | ExitFailureEffect
)
