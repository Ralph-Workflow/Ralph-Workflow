"""Pipeline events representing all state transitions.

Events are organized into logical categories for type-safe routing
to category-specific reducers. Each category has a dedicated enum.

This module mirrors the PipelineEvent enum from the Rust reducer/event
module.
"""

from __future__ import annotations

from enum import StrEnum


class PipelineEvent(StrEnum):
    """Top-level pipeline events.

    These events represent discrete state transitions that drive the pipeline.
    Events are processed by the reducer to compute new state.

    Attributes:
        AGENT_SUCCESS: Agent completed successfully.
        AGENT_FAILURE: Agent failed.
        AGENT_RETRY: Agent should be retried.
        ANALYSIS_SUCCESS: Analysis phase decided to advance (continue/success/approve).
        ANALYSIS_LOOPBACK: Analysis phase decided to loop (retry/request_changes).
        REVIEW_CLEAN: Review found no issues.
        REVIEW_ISSUES_FOUND: Review found issues requiring fix.
        FIX_SUCCESS: Fix agent completed successfully.
        FIX_FAILURE: Fix agent failed.
        COMMIT_SUCCESS: Commit completed successfully.
        COMMIT_FAILURE: Commit failed.
        CHECKPOINT_SAVED: Checkpoint was saved.
        CONTEXT_CLEANED: Context cleanup completed.
        INTERRUPTED: Pipeline was interrupted by user.
        COMPLETE: Pipeline completed successfully.
        FAILED: Pipeline failed.
        PROMPT_PREPARED: Prompt was prepared for the next agent invocation.
        PHASE_ADVANCE: Request to advance to the next phase.
    """

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


# Type alias for all events
Event = PipelineEvent
