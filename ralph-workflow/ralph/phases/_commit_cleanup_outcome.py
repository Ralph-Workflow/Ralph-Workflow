"""Outcome calculation for the commit-cleanup phase."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.pipeline.events import Event, PhaseFailureEvent, PipelineEvent
from ralph.recovery.classifier import FailureCategory

if TYPE_CHECKING:
    from ralph.mcp.artifacts._commit_cleanup import CommitCleanup


def build_cleanup_retry_hint(skipped_paths: list[str], safe_applied_count: int) -> str:
    """Build a structured retry hint naming rejected paths."""
    if not skipped_paths:
        return (
            "Cleanup retry hint: no delete actions were rejected, but the phase "
            "still failed. Check the artifact content for schema errors."
        )
    rendered_paths = "\n".join(f"  - {path!r}" for path in skipped_paths)
    safe_summary = (
        f"Safe actions applied: {safe_applied_count}"
        if safe_applied_count > 0
        else "No safe actions were applied alongside the rejected deletes."
    )
    return (
        "Cleanup retry hint: the following delete_file actions were rejected because "
        "they target files that look like source code, test files, documentation, "
        "or otherwise non-housekeeping content. Resubmit a commit_cleanup artifact "
        "that either (a) drops these paths from the actions list, (b) reclassifies "
        "them as add_to_git_exclude for machine-local files, or (c) reclassifies "
        "them as add_to_gitignore for project-wide patterns.\n"
        f"Rejected paths:\n{rendered_paths}\n"
        f"{safe_summary}"
    )


def decide_cleanup_outcome(
    phase_name: str,
    cleanup: CommitCleanup,
    skipped_delete_paths: list[str],
    failed_delete_paths: list[str] | None = None,
) -> list[Event]:
    """Return the final phase event after cleanup action application."""
    failed_paths = list(failed_delete_paths) if failed_delete_paths else []
    safe_actions_count = _count_safe_actions(cleanup, skipped_delete_paths, failed_paths)
    attempted_delete_count = _count_attempted_delete_actions(cleanup, skipped_delete_paths)
    if failed_paths and safe_actions_count == 0 and attempted_delete_count > 0:
        return _all_deletes_failed_failure(phase_name, failed_paths, safe_actions_count)
    delete_actions_count = _count_meaningful_delete_actions(cleanup)
    if skipped_delete_paths and safe_actions_count == 0 and delete_actions_count > 0:
        return _all_deletes_rejected_failure(
            phase_name, skipped_delete_paths, safe_actions_count
        )
    return _analysis_complete_outcome(cleanup)


def _count_safe_actions(
    cleanup: CommitCleanup,
    skipped_delete_paths: list[str],
    failed_delete_paths: list[str] | None = None,
) -> int:
    """Count meaningful actions that applied successfully."""
    skipped_set = set(skipped_delete_paths)
    failed_set = set(failed_delete_paths) if failed_delete_paths else set()
    return (
        sum(
            1
            for action in cleanup.actions
            if action.action == "add_to_gitignore"
            and action.pattern
            and action.pattern.strip()
        )
        + sum(
            1
            for action in cleanup.actions
            if action.action == "add_to_git_exclude"
            and action.pattern
            and action.pattern.strip()
        )
        + sum(
            1
            for action in cleanup.actions
            if (
                action.action == "delete_file"
                and action.path
                and action.path.strip()
                and action.path not in skipped_set
                and action.path not in failed_set
            )
        )
    )


def _count_meaningful_delete_actions(cleanup: CommitCleanup) -> int:
    """Count delete actions with non-whitespace paths."""
    return sum(
        1
        for action in cleanup.actions
        if action.action == "delete_file" and action.path and action.path.strip()
    )


def _count_attempted_delete_actions(
    cleanup: CommitCleanup,
    skipped_delete_paths: list[str],
) -> int:
    """Count meaningful delete actions accepted by the safety classifier."""
    skipped_set = set(skipped_delete_paths)
    return sum(
        1
        for action in cleanup.actions
        if (
            action.action == "delete_file"
            and action.path
            and action.path.strip()
            and action.path not in skipped_set
        )
    )


def _all_deletes_rejected_failure(
    phase_name: str,
    skipped_delete_paths: list[str],
    safe_actions_count: int,
) -> list[Event]:
    """Return a recoverable failure for a wholly rejected delete batch."""
    retry_hint = build_cleanup_retry_hint(skipped_delete_paths, safe_actions_count)
    logger.warning(
        "{}: all delete actions rejected. Returning PhaseFailureEvent with retry hint.",
        phase_name,
    )
    return [
        PhaseFailureEvent(
            phase=phase_name,
            reason=retry_hint,
            recoverable=True,
            retry_in_session=True,
            failure_category=FailureCategory.ARTIFACT_VALIDATION,
        )
    ]


def _all_deletes_failed_failure(
    phase_name: str,
    failed_delete_paths: list[str],
    safe_actions_count: int,
) -> list[Event]:
    """Return a recoverable failure when every attempted delete failed."""
    retry_hint = build_cleanup_retry_hint(failed_delete_paths, safe_actions_count)
    logger.warning(
        "{}: all attempted delete_file actions failed at apply time. "
        "Returning PhaseFailureEvent with retry hint.",
        phase_name,
    )
    return [
        PhaseFailureEvent(
            phase=phase_name,
            reason=retry_hint,
            recoverable=True,
            retry_in_session=True,
            failure_category=FailureCategory.ARTIFACT_VALIDATION,
        )
    ]


def _analysis_complete_outcome(cleanup: CommitCleanup) -> list[Event]:
    """Return success for complete analysis, otherwise loop back."""
    if cleanup.analysis_complete:
        return [PipelineEvent.AGENT_SUCCESS]
    return [PipelineEvent.PHASE_LOOPBACK]
