"""Effect types: what the pipeline wants to do next.

Effects are emitted by the orchestrator and describe the next action
to be taken. They carry all necessary data for the effect handler
to execute the action.

No I/O is performed in this module - effects are pure data descriptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.config.enums import PipelinePhase
    from ralph.pipeline.work_units import WorkUnit
    from ralph.pipeline.worker_state import WorkerState
else:
    Mapping = import_module("collections.abc").Mapping
    PipelinePhase = import_module("ralph.config.enums").PipelinePhase
    WorkUnit = import_module("ralph.pipeline.work_units").WorkUnit
    WorkerState = import_module("ralph.pipeline.worker_state").WorkerState


# Forbidden sentinel strings that must never appear as ExitFailureEffect.reason.
# These indicate bugs in the pipeline where descriptive error information was lost.
_FORBIDDEN_SENTINELS: frozenset[str] = frozenset({
    "Unknown failure",
    "unknown failure",
    "",
    "None",
    "null",
})


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
    drain: str | None = None
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
    drain: str | None = None


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
        reason: Reason for the failure. Must be non-empty, non-whitespace,
            and must not be any known sentinel that indicates a bug (e.g.
            "Unknown failure", "", "None", "null").
    """

    reason: str

    def __post_init__(self) -> None:
        """Validate that reason is non-empty, non-whitespace, and not a forbidden sentinel."""
        stripped = self.reason.strip()
        if stripped == "" or self.reason in _FORBIDDEN_SENTINELS:
            raise ValueError(
                f"ExitFailureEffect.reason must be descriptive and cannot be a sentinel; "
                f"got: {self.reason!r} (whitespace stripped: {stripped!r})"
            )


@dataclass(frozen=True)
class FanOutDevelopmentEffect:
    """Effect to fan out development work across workers."""

    work_units: tuple[WorkUnit, ...]
    max_workers: int


@dataclass(frozen=True)
class MergeIntegrationEffect:
    """Effect to merge results from parallel workers."""

    worker_states: Mapping[str, WorkerState]
    base_branch: str


# Union type for all effects
Effect = (
    InvokeAgentEffect
    | PreparePromptEffect
    | CommitEffect
    | PushEffect
    | SaveCheckpointEffect
    | ExitSuccessEffect
    | ExitFailureEffect
    | FanOutDevelopmentEffect
    | MergeIntegrationEffect
)
