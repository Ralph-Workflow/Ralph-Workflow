from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.phases._commit_review_events import CommitReviewLoopback

if TYPE_CHECKING:
    from ralph.phases._commit_review_actions import CommitReviewApplyReport


def build_commit_review_retry_hint(skipped_paths: list[str], safe_applied_count: int) -> str:
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


def build_unapplied_retry_hint(report: CommitReviewApplyReport) -> str:
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
    report: CommitReviewApplyReport,
) -> CommitReviewLoopback | None:
    if _has_failed_applications(report):
        logger.warning(
            "{}: requested commit-review actions failed at apply time; looping back",
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
            hint=build_commit_review_retry_hint(report.declined_delete_paths, report.applied_count)
        )
    return None


def _has_failed_applications(report: CommitReviewApplyReport) -> bool:
    return bool(report.failed_delete_paths or report.failed_pattern_tokens)


__all__ = [
    "build_commit_review_retry_hint",
    "build_unapplied_retry_hint",
    "decide_commit_review_outcome",
]
