"""Outcome calculation for the commit-cleanup phase."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.pipeline.events import Event, PhaseFailureEvent, PipelineEvent
from ralph.recovery.classifier import FailureCategory

if TYPE_CHECKING:
    from ralph.mcp.artifacts._commit_cleanup import CommitCleanup
    from ralph.phases._commit_cleanup_actions import CleanupApplyReport


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


def build_unapplied_retry_hint(report: CleanupApplyReport) -> str:
    """Build the hint delivered to the next prompt for unapplied decisions."""
    lines: list[str] = ["Cleanup retry hint: some requested actions were not applied."]
    if report.declined_delete_paths:
        lines.append("Declined delete_file paths:")
        lines.extend(f"  - {path!r}" for path in report.declined_delete_paths)
        lines.append(
            "Resubmit without these paths, or reclassify a machine-local path as "
            "add_to_git_exclude / a project-wide pattern as add_to_gitignore."
        )
    if report.failed_delete_paths:
        lines.append("delete_file actions that failed while applying:")
        lines.extend(f"  - {path!r}" for path in report.failed_delete_paths)
    if report.failed_gitignore_patterns:
        lines.append("add_to_gitignore patterns that failed while applying:")
        lines.extend(f"  - {pattern!r}" for pattern in report.failed_gitignore_patterns)
    if report.failed_exclude_patterns:
        lines.append("add_to_git_exclude patterns that failed while applying:")
        lines.extend(f"  - {pattern!r}" for pattern in report.failed_exclude_patterns)
    if report.unapplied_notes:
        lines.append("Other unapplied decisions:")
        lines.extend(f"  - {note}" for note in report.unapplied_notes)
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def decide_cleanup_outcome(
    phase_name: str,
    cleanup: CommitCleanup,
    report: CleanupApplyReport,
) -> list[Event]:
    """Return the final phase event after cleanup action application."""
    if all_applications_failed(report):
        tokens = [*report.failed_delete_paths, *report.failed_pattern_tokens]
        return _recoverable_housekeeping_failure(
            phase_name,
            "all cleanup applications failed at apply time",
            tokens,
        )
    return _analysis_complete_outcome(cleanup)


def all_applications_failed(report: CleanupApplyReport) -> bool:
    """Return True when every attempted apply threw and nothing applied."""
    attempted = (
        len(report.failed_delete_paths)
        + len(report.failed_pattern_tokens)
        + report.applied_count
    )
    return report.applied_count == 0 and attempted > 0


def _recoverable_housekeeping_failure(
    phase_name: str,
    label: str,
    tokens: list[str],
) -> list[Event]:
    """Return a recoverable failure that must not terminate the run by itself."""
    retry_hint = build_cleanup_retry_hint(tokens, 0)
    logger.warning("{}: {}. Returning PhaseFailureEvent with retry hint.", phase_name, label)
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
