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
    from ralph.config.enums import PipelinePhase
    from ralph.pipeline.work_units import WorkUnit
else:
    PipelinePhase = import_module("ralph.config.enums").PipelinePhase
    WorkUnit = import_module("ralph.pipeline.work_units").WorkUnit


# Forbidden non-empty sentinel strings that must never appear inside
# ExitFailureEffect.reason. Empty and whitespace-only values are validated
# separately in __post_init__ so the substring check does not reject every
# possible reason via "" in reason.
_FORBIDDEN_SENTINELS: frozenset[str] = frozenset(
    {
        "Unknown failure",
        "unknown failure",
        "None",
        "null",
    }
)


def _contains_forbidden_sentinel(reason: str) -> tuple[bool, str | None]:
    """Check if reason contains any forbidden sentinel as a substring.

    Returns:
        Tuple of (is_forbidden, matched_sentinel). matched_sentinel is the
        specific sentinel that was found, or None if no sentinel found.
    """
    for sentinel in _FORBIDDEN_SENTINELS:
        if sentinel in reason:
            return True, sentinel
    return False, None


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
            and must not contain any known non-empty sentinel that indicates
            a bug (e.g. "Unknown failure", "None", "null"). Empty and
            whitespace-only reasons are rejected separately. Sentinel checks
            are performed as substring matches to catch cases like
            "development: Unknown failure".
    """

    reason: str

    def __post_init__(self) -> None:
        """Validate that reason is non-empty, non-whitespace, and not a forbidden sentinel."""
        stripped = self.reason.strip()
        if stripped == "":
            raise ValueError(
                f"ExitFailureEffect.reason must be descriptive and cannot be empty or whitespace; "
                f"got: {self.reason!r} (whitespace stripped: {stripped!r})"
            )

        # Check for forbidden sentinels as substrings
        is_forbidden, matched = _contains_forbidden_sentinel(self.reason)
        if is_forbidden:
            raise ValueError(
                "ExitFailureEffect.reason must be descriptive and cannot contain "
                f"a forbidden sentinel; matched sentinel: {matched!r} "
                f"in reason: {self.reason!r}"
            )


@dataclass(frozen=True)
class FanOutEffect:
    """Effect to fan out parallel work for any phase whose [parallelization] policy is declared.

    Workers run in the shared checkout. Each worker is restricted to its declared
    ``allowed_directories`` and writes its outputs to a per-worker namespace under
    ``.agent/workers/<unit_id>/``.

    Attributes:
        work_units: Work units to execute in parallel.
        max_workers: Maximum number of concurrent workers.
        run_post_fanout_verification: When True, the runner will execute a serialized
            workspace-wide verification step after all workers finish. Defaults to False
            so unit tests do not invoke ``make verify``.
        phase: The pipeline phase for which fan-out is occurring. Defaults to empty
            string for backward compatibility; the runner always populates this.
    """

    work_units: tuple[WorkUnit, ...]
    max_workers: int
    run_post_fanout_verification: bool = False
    phase: str = ""


def __getattr__(name: str) -> object:
    if name == "FanOutDevelopmentEffect":
        import warnings  # noqa: PLC0415
        warnings.warn(
            "FanOutDevelopmentEffect is deprecated; use FanOutEffect instead. "
            "# reason: deprecation alias",
            DeprecationWarning,
            stacklevel=2,
        )
        return FanOutEffect
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Union type for all effects
Effect = (
    InvokeAgentEffect
    | PreparePromptEffect
    | CommitEffect
    | PushEffect
    | SaveCheckpointEffect
    | ExitSuccessEffect
    | ExitFailureEffect
    | FanOutEffect
)
