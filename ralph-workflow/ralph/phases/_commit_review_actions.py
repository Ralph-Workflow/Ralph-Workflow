
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ralph.mcp.artifacts._commit_review_action import CommitReviewAction


class _CommitReviewLike(Protocol):

    @property
    def actions(self) -> Sequence[CommitReviewAction]: ...


@dataclass
class CommitReviewApplyReport:

    declined_delete_paths: list[str] = field(default_factory=list)
    failed_delete_paths: list[str] = field(default_factory=list)
    applied_delete_paths: list[str] = field(default_factory=list)
    applied_gitignore_patterns: list[str] = field(default_factory=list)
    failed_gitignore_patterns: list[str] = field(default_factory=list)
    applied_exclude_patterns: list[str] = field(default_factory=list)
    failed_exclude_patterns: list[str] = field(default_factory=list)
    unapplied_notes: list[str] = field(default_factory=list)

    @property
    def applied_count(self) -> int:
        return (
            len(self.applied_delete_paths)
            + len(self.applied_gitignore_patterns)
            + len(self.applied_exclude_patterns)
        )

    @property
    def failed_pattern_tokens(self) -> list[str]:
        return [*self.failed_gitignore_patterns, *self.failed_exclude_patterns]


def apply_commit_review_actions(
    repo_root: Path,
    commit_review: _CommitReviewLike,
    *,
    is_safe_to_delete: Callable[[Path, str], bool],
    append_to_gitignore: Callable[[Path, list[str]], None],
    add_to_git_exclude: Callable[[Path, list[str]], None],
    delete_file_from_repo: Callable[[Path, str], None],
) -> CommitReviewApplyReport:
    report = CommitReviewApplyReport()
    gitignore_patterns: list[str] = []
    git_exclude_patterns: list[str] = []
    safe_delete_files: list[str] = []

    for action in commit_review.actions:
        _classify_action(
            action,
            repo_root,
            gitignore_patterns,
            git_exclude_patterns,
            safe_delete_files,
            report,
            is_safe_to_delete=is_safe_to_delete,
        )

    _apply_gitignore_patterns(
        repo_root,
        gitignore_patterns,
        report,
        append_to_gitignore=append_to_gitignore,
    )
    _apply_git_exclude_patterns(
        repo_root,
        git_exclude_patterns,
        report,
        add_to_git_exclude=add_to_git_exclude,
    )
    _apply_safe_deletes(
        repo_root,
        safe_delete_files,
        report,
        delete_file_from_repo=delete_file_from_repo,
    )
    return report


def _classify_action(
    action: CommitReviewAction,
    repo_root: Path,
    gitignore_patterns: list[str],
    git_exclude_patterns: list[str],
    safe_delete_files: list[str],
    report: CommitReviewApplyReport,
    *,
    is_safe_to_delete: Callable[[Path, str], bool],
) -> None:
    act_type = action.action
    if act_type == "add_to_gitignore":
        pattern = action.pattern
        if pattern and pattern.strip():
            gitignore_patterns.append(pattern)
        else:
            note = "empty add_to_gitignore pattern"
            logger.warning("Skipping add_to_gitignore action with empty/whitespace pattern")
            _note_once(report, note)
        return
    if act_type == "add_to_git_exclude":
        pattern = action.pattern
        if pattern and pattern.strip():
            git_exclude_patterns.append(pattern)
        else:
            note = "empty add_to_git_exclude pattern"
            logger.warning("Skipping add_to_git_exclude action with empty/whitespace pattern")
            _note_once(report, note)
        return
    if act_type == "delete_file":
        path = action.path
        if not path or not path.strip():
            note = "empty delete_file path"
            logger.warning("Skipping delete_file action with empty/whitespace path")
            _note_once(report, note)
            return
        if not is_safe_to_delete(repo_root, path):
            if path in report.declined_delete_paths:
                note = f"duplicate declined delete_file:{path}"
                logger.warning("Skipping duplicate delete_file action for: {}", path)
                _note_once(report, note)
                return
            logger.warning(
                "Skipping unsafe delete_file action for {!r} "
                "(target does not match the engine housekeeping allowlist). "
                "The rest of the commit-review action batch will continue.",
                path,
            )
            report.declined_delete_paths.append(path)
            return
        safe_delete_files.append(path)


def _apply_gitignore_patterns(
    repo_root: Path,
    patterns: list[str],
    report: CommitReviewApplyReport,
    *,
    append_to_gitignore: Callable[[Path, list[str]], None],
) -> None:
    for pattern in patterns:
        try:
            append_to_gitignore(repo_root, [pattern])
            report.applied_gitignore_patterns.append(pattern)
            logger.debug("Added pattern to .gitignore: {}", pattern)
        except Exception as exc:
            report.failed_gitignore_patterns.append(pattern)
            logger.warning("Failed to append pattern to .gitignore ({}): {}", pattern, exc)


def _apply_git_exclude_patterns(
    repo_root: Path,
    patterns: list[str],
    report: CommitReviewApplyReport,
    *,
    add_to_git_exclude: Callable[[Path, list[str]], None],
) -> None:
    for pattern in patterns:
        try:
            add_to_git_exclude(repo_root, [pattern])
            report.applied_exclude_patterns.append(pattern)
            logger.debug("Added pattern to .git/info/exclude: {}", pattern)
        except Exception as exc:
            report.failed_exclude_patterns.append(pattern)
            logger.warning("Failed to append pattern to .git/info/exclude ({}): {}", pattern, exc)


def _apply_safe_deletes(
    repo_root: Path,
    safe_delete_files: list[str],
    report: CommitReviewApplyReport,
    *,
    delete_file_from_repo: Callable[[Path, str], None],
) -> None:
    seen_paths: set[str] = set()
    for file_path in safe_delete_files:
        if file_path in seen_paths:
            note = f"duplicate delete_file:{file_path}"
            logger.warning("Skipping duplicate delete_file action for: {}", file_path)
            _note_once(report, note)
            continue
        seen_paths.add(file_path)
        try:
            delete_file_from_repo(repo_root, file_path)
            report.applied_delete_paths.append(file_path)
            logger.debug("Deleted file: {}", file_path)
        except Exception as exc:
            report.failed_delete_paths.append(file_path)
            logger.warning("Failed to delete file {!r} (continuing batch): {}", file_path, exc)


def _note_once(report: CommitReviewApplyReport, note: str) -> None:
    if note not in report.unapplied_notes:
        report.unapplied_notes.append(note)


__all__ = [
    "CommitReviewApplyReport",
    "apply_commit_review_actions",
]
