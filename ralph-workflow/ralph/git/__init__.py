"""Public Git helpers for Ralph.

This package exposes the repository operations used by the Python orchestrator,
including staging/commit helpers, managed hook installation, and rebase support.
Implementation uses GitPython rather than the retired Rust/libgit2 stack.
"""

from ralph.git.git_run_result import GitRunResult
from ralph.git.hooks import (
    HOOK_MARKER,
    RALPH_HOOK_NAMES,
    get_hooks_dir,
    install_hooks,
    install_hooks_in_repo,
    reinstall_hooks_if_tampered,
    uninstall_hooks,
)
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
from ralph.git.subprocess_runner import run_git

from .wrapper import (
    GitHelpers,
    detect_unauthorized_commit,
    end_agent_phase,
    start_agent_phase,
)

__all__ = [
    "HOOK_MARKER",
    "RALPH_HOOK_NAMES",
    "GitHelpers",
    "GitOperationError",
    "GitRunResult",
    "append_to_gitignore",
    "create_commit",
    "detect_unauthorized_commit",
    "end_agent_phase",
    "find_repo_root",
    "get_head_sha",
    "get_hooks_dir",
    "get_staged_files",
    "has_staged_changes",
    "install_hooks",
    "install_hooks_in_repo",
    "is_repo_clean",
    "merge_base",
    "push",
    "reinstall_hooks_if_tampered",
    "run_git",
    "stage_all",
    "start_agent_phase",
    "uninstall_hooks",
]
