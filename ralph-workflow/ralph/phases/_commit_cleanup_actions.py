"""Action classification and application for the commit-cleanup phase."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.mcp.artifacts._commit_cleanup import CommitCleanup
    from ralph.mcp.artifacts._commit_cleanup_action import CommitCleanupAction


def apply_cleanup_actions(
    repo_root: Path,
    cleanup: CommitCleanup,
    *,
    is_safe_to_delete: Callable[[Path, str], bool],
    append_to_gitignore: Callable[[Path, list[str]], None],
    add_to_git_exclude: Callable[[Path, list[str]], None],
    delete_file_from_repo: Callable[[Path, str], None],
) -> tuple[list[str], list[str]]:
    """Apply cleanup actions and return rejected and apply-time-failed paths."""
    gitignore_patterns: list[str] = []
    git_exclude_patterns: list[str] = []
    safe_delete_files: list[str] = []
    skipped_delete_paths: list[str] = []

    for action in cleanup.actions:
        _classify_action(
            action,
            repo_root,
            gitignore_patterns,
            git_exclude_patterns,
            safe_delete_files,
            skipped_delete_paths,
            is_safe_to_delete=is_safe_to_delete,
        )

    _apply_gitignore_patterns(
        repo_root,
        gitignore_patterns,
        append_to_gitignore=append_to_gitignore,
    )
    _apply_git_exclude_patterns(
        repo_root,
        git_exclude_patterns,
        add_to_git_exclude=add_to_git_exclude,
    )
    _succeeded, failed_delete_paths = _apply_safe_deletes(
        repo_root,
        safe_delete_files,
        delete_file_from_repo=delete_file_from_repo,
    )
    return skipped_delete_paths, failed_delete_paths


def _classify_action(
    action: CommitCleanupAction,
    repo_root: Path,
    gitignore_patterns: list[str],
    git_exclude_patterns: list[str],
    safe_delete_files: list[str],
    skipped_delete_paths: list[str],
    *,
    is_safe_to_delete: Callable[[Path, str], bool],
) -> None:
    """Route one cleanup action into the appropriate output bucket."""
    act_type = action.action
    if act_type == "add_to_gitignore":
        pattern = action.pattern
        if pattern and pattern.strip():
            gitignore_patterns.append(pattern)
        else:
            logger.debug("Skipping add_to_gitignore action with empty/whitespace pattern")
        return
    if act_type == "add_to_git_exclude":
        pattern = action.pattern
        if pattern and pattern.strip():
            git_exclude_patterns.append(pattern)
        else:
            logger.debug("Skipping add_to_git_exclude action with empty/whitespace pattern")
        return
    if act_type == "delete_file":
        path = action.path
        if not path or not path.strip():
            logger.debug("Skipping delete_file action with empty/whitespace path")
            return
        if not is_safe_to_delete(repo_root, path):
            logger.warning(
                "Skipping unsafe delete_file action for {!r} "
                "(target does not match the engine housekeeping allowlist). "
                "The rest of the cleanup batch will continue.",
                path,
            )
            skipped_delete_paths.append(path)
            return
        safe_delete_files.append(path)


def _apply_gitignore_patterns(
    repo_root: Path,
    patterns: list[str],
    *,
    append_to_gitignore: Callable[[Path, list[str]], None],
) -> None:
    """Append gitignore patterns with per-pattern exception isolation."""
    for pattern in patterns:
        try:
            append_to_gitignore(repo_root, [pattern])
            logger.debug("Added pattern to .gitignore: {}", pattern)
        except Exception as exc:
            logger.warning("Failed to append pattern to .gitignore ({}): {}", pattern, exc)


def _apply_git_exclude_patterns(
    repo_root: Path,
    patterns: list[str],
    *,
    add_to_git_exclude: Callable[[Path, list[str]], None],
) -> None:
    """Append git-exclude patterns with per-pattern exception isolation."""
    for pattern in patterns:
        try:
            add_to_git_exclude(repo_root, [pattern])
            logger.debug("Added pattern to .git/info/exclude: {}", pattern)
        except Exception as exc:
            logger.warning(
                "Failed to append pattern to .git/info/exclude ({}): {}", pattern, exc
            )


def _apply_safe_deletes(
    repo_root: Path,
    safe_delete_files: list[str],
    *,
    delete_file_from_repo: Callable[[Path, str], None],
) -> tuple[list[str], list[str]]:
    """Apply deduplicated safe deletes, isolating each failure."""
    succeeded: list[str] = []
    failed: list[str] = []
    seen_paths: set[str] = set()
    for file_path in safe_delete_files:
        if file_path in seen_paths:
            logger.debug("Skipping duplicate delete_file action for: {}", file_path)
            continue
        seen_paths.add(file_path)
        try:
            delete_file_from_repo(repo_root, file_path)
            succeeded.append(file_path)
            logger.debug("Deleted file: {}", file_path)
        except Exception as exc:
            failed.append(file_path)
            logger.warning(
                "Failed to delete file {!r} (continuing batch): {}", file_path, exc
            )
    return succeeded, failed
