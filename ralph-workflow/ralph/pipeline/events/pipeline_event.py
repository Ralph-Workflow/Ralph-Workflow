"""Pipeline event type enumeration."""

from enum import StrEnum


class PipelineEvent(StrEnum):
    """Enumeration of all pipeline state-machine transition events."""

    AGENT_SUCCESS = "agent_success"
    AGENT_FAILURE = "agent_failure"
    AGENT_RETRY = "agent_retry"
    ANALYSIS_SUCCESS = "analysis_success"
    ANALYSIS_LOOPBACK = "analysis_loopback"
    PHASE_LOOPBACK = "phase_loopback"
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
