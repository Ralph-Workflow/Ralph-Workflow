"""Git operations package for Ralph pipeline."""

# pyright: reportMissingImports=false

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
from .wrapper import (
    GitHelpers,
    detect_unauthorized_commit,
    end_agent_phase,
    start_agent_phase,
)

__all__ = [
    "GitOperationError",
    "append_to_gitignore",
    "create_commit",
    "find_repo_root",
    "get_head_sha",
    "get_hooks_dir",
    "get_staged_files",
    "has_staged_changes",
    "HOOK_MARKER",
    "install_hooks",
    "install_hooks_in_repo",
    "is_repo_clean",
    "merge_base",
    "RALPH_HOOK_NAMES",
    "push",
    "reinstall_hooks_if_tampered",
    "stage_all",
    "uninstall_hooks",
    "detect_unauthorized_commit",
    "end_agent_phase",
    "GitHelpers",
    "start_agent_phase",
]
