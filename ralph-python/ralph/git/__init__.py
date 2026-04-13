"""Git operations package for Ralph pipeline."""

from ralph.git.operations import (
    GitOperationError,
    append_to_gitignore,
    create_commit,
    find_repo_root,
    get_head_sha,
    get_staged_files,
    has_staged_changes,
    is_repo_clean,
    merge_base,
    push,
    stage_all,
)

__all__ = [
    "GitOperationError",
    "append_to_gitignore",
    "create_commit",
    "find_repo_root",
    "get_head_sha",
    "get_staged_files",
    "has_staged_changes",
    "is_repo_clean",
    "merge_base",
    "push",
    "stage_all",
]
