"""Pipeline events representing all state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PipelineEvent(StrEnum):
    """Enumeration of all pipeline state-machine transition events."""

    AGENT_SUCCESS = "agent_success"
    AGENT_FAILURE = "agent_failure"
    AGENT_RETRY = "agent_retry"
    ANALYSIS_SUCCESS = "analysis_success"
    ANALYSIS_LOOPBACK = "analysis_loopback"
    REVIEW_CLEAN = "review_clean"
    REVIEW_ISSUES_FOUND = "review_issues_found"
    FIX_SUCCESS = "fix_success"
    FIX_FAILURE = "fix_failure"
    COMMIT_SUCCESS = "commit_success"
    COMMIT_SKIPPED = "commit_skipped"
    COMMIT_FAILURE = "commit_failure"
    CHECKPOINT_SAVED = "checkpoint_saved"
    CONTEXT_CLEANED = "context_cleaned"
    INTERRUPTED = "interrupted"
    COMPLETE = "complete"
    FAILED = "failed"
    PROMPT_PREPARED = "prompt_prepared"
    PHASE_ADVANCE = "phase_advance"
    FAN_OUT_STARTED = "fan_out_started"
    WORKERS_RESUMED = "workers_resumed"
    ALL_WORKERS_COMPLETE = "all_workers_complete"


@dataclass(frozen=True)
class PhaseFailureEvent:
    """Event emitted when a phase handler encounters a failure condition.

    This event carries a recoverable flag that determines how the reducer
    processes the failure:
    - recoverable=True: routes through _handle_agent_failure retry/fallback logic
    - recoverable=False: routes directly to the terminal failure phase

    Attributes:
        phase: Name of the phase that generated this event.
        reason: Human-readable description of what caused the failure.
        recoverable: Whether this failure should trigger retry/fallback (True)
            or act as a terminal decision (False).
        retry_in_session: When True and the agent's transport supports session
            resume, the recovery path should preserve the active session ID so
            the next retry continues in the same agent session rather than
            starting from scratch. Only meaningful when recoverable=True.
    """

    phase: str
    reason: str
    recoverable: bool
    retry_in_session: bool = False


@dataclass(frozen=True)
class WorkerStartedEvent:
    """Emitted when a parallel worker begins execution."""

    unit_id: str


@dataclass(frozen=True)
class WorkerCompletedEvent:
    """Emitted when a parallel worker finishes successfully."""

    unit_id: str
    exit_code: int


@dataclass(frozen=True)
class WorkerFailedEvent:
    """Emitted when a parallel worker terminates with a failure."""

    unit_id: str
    exit_code: int
    error: str


@dataclass(frozen=True)
class PostFanoutVerificationEvent:
    """Event emitted after serialized workspace-wide verification runs post fan-out.

    Attributes:
        success: Whether verification passed (exit code 0).
        exit_code: The verification subprocess exit code, or None if not run.
        error: Human-readable error description when success=False, else None.
    """

    success: bool
    exit_code: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class AnalysisDecisionEvent:
    """Event emitted when an analysis phase resolves a decision from the agent artifact.

    The reducer routes via ``policy.phases[phase].decisions[decision].target`` directly,
    making this a first-class routing input rather than a collapsed signal.

    Attributes:
        phase: Name of the phase that emitted this decision.
        decision: Raw decision string from the agent artifact (validated against
            the phase's decision_vocabulary in the artifacts policy).
    """

    phase: str
    decision: str


Event = (
    PipelineEvent
    | PhaseFailureEvent
    | WorkerStartedEvent
    | WorkerCompletedEvent
    | WorkerFailedEvent
    | PostFanoutVerificationEvent
    | AnalysisDecisionEvent
)
