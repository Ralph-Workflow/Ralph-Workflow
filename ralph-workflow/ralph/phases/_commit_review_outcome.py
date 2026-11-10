"""Outcome calculation for the commit-review phase."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from loguru import logger

from ralph.phases._commit_review_events import CommitReviewEvent, CommitReviewLoopback
from ralph.pipeline.events import Event, PipelineEvent

if TYPE_CHECKING:
    from ralph.phases._commit_review_actions import CleanupApplyReport


@runtime_checkable
class _HasVerdict(Protocol):
    """Protocol for artifacts reporting a verdict."""

    verdict: str


def build_cleanup_retry_hint(skipped_paths: list[str], safe_applied_count: int) -> str:
    """Build a structured retry hint naming rejected paths."""
    if not skipped_paths:
        return (
            "Commit review retry hint: no delete actions were rejected, but the phase "
            "still failed. Check the artifact content for schema errors."
        )
    rendered_paths = "\n".join(f"  - {path!r}" for path in skipped_paths)
    safe_summary = (
        f"Safe actions applied: {safe_applied_count}"
        if safe_applied_count > 0
        else "No safe actions were applied alongside the rejected deletes."
    )
    return (
        "Commit review retry hint: the following delete_file actions were rejected because "
        "they target files that look like source code, test files, documentation, "
        "or otherwise non-housekeeping content. Resubmit a commit_review artifact "
        "that either (a) drops these paths from the actions list, (b) reclassifies "
        "them as add_to_git_exclude for machine-local files, or (c) reclassifies "
        "them as add_to_gitignore for project-wide patterns.\n"
        f"Rejected paths:\n{rendered_paths}\n"
        f"{safe_summary}"
    )


def build_unapplied_retry_hint(report: CleanupApplyReport) -> str:
    """Build the hint delivered to the next prompt for unapplied decisions."""
    lines: list[str] = ["Commit review retry hint: some requested actions were not applied."]
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


def decide_commit_review_outcome(
    phase_name: str,
    report: CleanupApplyReport,
) -> CommitReviewLoopback | None:
    """Return the loopback event when the applied batch needs another attempt.

    An apply-time failure and a safety-classifier rejection are different
    problems, so they get different hints: a rejected delete names the paths
    the classifier refused, while an apply-time failure names the paths that
    were accepted but could not be applied.
    """
    if all_applications_failed(report):
        logger.warning(
            "{}: every requested cleanup action failed at apply time; looping back",
            phase_name,
        )
        return CommitReviewLoopback(hint=build_unapplied_retry_hint(report))
    if report.declined_delete_paths:
        logger.warning(
            "{}: unsafe delete actions declined: {}",
            phase_name,
            report.declined_delete_paths,
        )
        return CommitReviewLoopback(
            hint=build_cleanup_retry_hint(report.declined_delete_paths, report.applied_count)
        )
    return None


def decide_cleanup_outcome(
    phase_name: str,
    cleanup: object,
    report: CleanupApplyReport,
) -> list[Event | CommitReviewEvent]:
    """Return the phase events for one applied cleanup batch.

    The batch decision comes first: when the agent has to try again the
    batch wins, otherwise the artifact's verdict decides the outcome.
    """
    loopback = decide_commit_review_outcome(phase_name, report)
    if loopback is not None:
        return [loopback]
    return _verdict_outcome(cleanup)


def all_applications_failed(report: CleanupApplyReport) -> bool:
    """Return True when every attempted apply threw and nothing applied."""
    attempted = (
        len(report.failed_delete_paths) + len(report.failed_pattern_tokens) + report.applied_count
    )
    return report.applied_count == 0 and attempted > 0


def _verdict_outcome(cleanup: object) -> list[Event | CommitReviewEvent]:
    """Return success for an approving verdict, otherwise loop back."""
    if isinstance(cleanup, _HasVerdict) and cleanup.verdict == "approve":
        return [PipelineEvent.AGENT_SUCCESS]
    return [PipelineEvent.PHASE_LOOPBACK]


__all__ = [
    "all_applications_failed",
    "build_cleanup_retry_hint",
    "build_unapplied_retry_hint",
    "decide_cleanup_outcome",
    "decide_commit_review_outcome",
]
