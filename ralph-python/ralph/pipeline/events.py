"""Pipeline events representing all state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PipelineEvent(StrEnum):
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
    COMMIT_FAILURE = "commit_failure"
    CHECKPOINT_SAVED = "checkpoint_saved"
    CONTEXT_CLEANED = "context_cleaned"
    INTERRUPTED = "interrupted"
    COMPLETE = "complete"
    FAILED = "failed"
    PROMPT_PREPARED = "prompt_prepared"
    PHASE_ADVANCE = "phase_advance"
    FAN_OUT_STARTED = "fan_out_started"
    ALL_WORKERS_COMPLETE = "all_workers_complete"


@dataclass(frozen=True)
class WorkerStartedEvent:
    unit_id: str


@dataclass(frozen=True)
class WorkerCompletedEvent:
    unit_id: str
    exit_code: int
    commit_sha: str


@dataclass(frozen=True)
class WorkerFailedEvent:
    unit_id: str
    exit_code: int
    error: str


@dataclass(frozen=True)
class WorkersMergeConflictEvent:
    conflicting_unit_ids: list[str]


Event = (
    PipelineEvent
    | WorkerStartedEvent
    | WorkerCompletedEvent
    | WorkerFailedEvent
    | WorkersMergeConflictEvent
)
