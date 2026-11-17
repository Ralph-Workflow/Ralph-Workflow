from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.git.commit_result import CommitCreationResult, CommitCreationStatus
    from ralph.git.errors import GitOperationError
    from ralph.git.git_command_queue import (
        GitCommandQueue,
        QueuedCallOutcome,
        WorktreeLease,
        WorktreeLeaseHolder,
        WorktreeLeaseUnavailable,
        begin_agent_lease,
        end_agent_lease,
        get_queue_for_repo,
        is_lease_held,
        reset_all_queues,
        route_git_method,
        route_run_git,
        try_acquire_lease,
    )
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
        append_to_gitignore,
        atomic_append_text,
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
    "CommitCreationResult",
    "CommitCreationStatus",
    "GitCommandQueue",
    "GitHelpers",
    "GitOperationError",
    "GitRunResult",
    "QueuedCallOutcome",
    "WorktreeLease",
    "WorktreeLeaseHolder",
    "WorktreeLeaseUnavailable",
    "append_to_gitignore",
    "atomic_append_text",
    "begin_agent_lease",
    "create_commit",
    "detect_unauthorized_commit",
    "end_agent_lease",
    "end_agent_phase",
    "find_repo_root",
    "get_head_sha",
    "get_hooks_dir",
    "get_queue_for_repo",
    "get_staged_files",
    "has_staged_changes",
    "install_hooks",
    "install_hooks_in_repo",
    "is_lease_held",
    "is_repo_clean",
    "lock_recovery",
    "merge_base",
    "push",
    "reinstall_hooks_if_tampered",
    "reset_all_queues",
    "route_git_method",
    "route_run_git",
    "run_git",
    "stage_all",
    "start_agent_phase",
    "try_acquire_lease",
    "uninstall_hooks",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "HOOK_MARKER": ("ralph.git.hooks", "HOOK_MARKER"),
    "RALPH_HOOK_NAMES": ("ralph.git.hooks", "RALPH_HOOK_NAMES"),
    "CommitCreationResult": ("ralph.git.commit_result", "CommitCreationResult"),
    "CommitCreationStatus": ("ralph.git.commit_result", "CommitCreationStatus"),
    "GitCommandQueue": ("ralph.git.git_command_queue", "GitCommandQueue"),
    "GitHelpers": ("ralph.git.wrapper", "GitHelpers"),
    "GitOperationError": ("ralph.git.errors", "GitOperationError"),
    "GitRunResult": ("ralph.git.git_run_result", "GitRunResult"),
    "QueuedCallOutcome": ("ralph.git.git_command_queue", "QueuedCallOutcome"),
    "WorktreeLease": ("ralph.git.git_command_queue", "WorktreeLease"),
    "WorktreeLeaseHolder": ("ralph.git.git_command_queue", "WorktreeLeaseHolder"),
    "WorktreeLeaseUnavailable": ("ralph.git.git_command_queue", "WorktreeLeaseUnavailable"),
    "append_to_gitignore": ("ralph.git.operations", "append_to_gitignore"),
    "atomic_append_text": ("ralph.git.operations", "atomic_append_text"),
    "begin_agent_lease": ("ralph.git.git_command_queue", "begin_agent_lease"),
    "create_commit": ("ralph.git.operations", "create_commit"),
    "detect_unauthorized_commit": ("ralph.git.wrapper", "detect_unauthorized_commit"),
    "end_agent_lease": ("ralph.git.git_command_queue", "end_agent_lease"),
    "end_agent_phase": ("ralph.git.wrapper", "end_agent_phase"),
    "find_repo_root": ("ralph.git.operations", "find_repo_root"),
    "get_head_sha": ("ralph.git.operations", "get_head_sha"),
    "get_hooks_dir": ("ralph.git.hooks", "get_hooks_dir"),
    "get_queue_for_repo": ("ralph.git.git_command_queue", "get_queue_for_repo"),
    "get_staged_files": ("ralph.git.operations", "get_staged_files"),
    "has_staged_changes": ("ralph.git.operations", "has_staged_changes"),
    "install_hooks": ("ralph.git.hooks", "install_hooks"),
    "install_hooks_in_repo": ("ralph.git.hooks", "install_hooks_in_repo"),
    "is_lease_held": ("ralph.git.git_command_queue", "is_lease_held"),
    "is_repo_clean": ("ralph.git.operations", "is_repo_clean"),
    "merge_base": ("ralph.git.operations", "merge_base"),
    "push": ("ralph.git.operations", "push"),
    "reinstall_hooks_if_tampered": ("ralph.git.hooks", "reinstall_hooks_if_tampered"),
    "reset_all_queues": ("ralph.git.git_command_queue", "reset_all_queues"),
    "route_git_method": ("ralph.git.git_command_queue", "route_git_method"),
    "route_run_git": ("ralph.git.git_command_queue", "route_run_git"),
    "run_git": ("ralph.git.subprocess_runner", "run_git"),
    "stage_all": ("ralph.git.operations", "stage_all"),
    "start_agent_phase": ("ralph.git.wrapper", "start_agent_phase"),
    "try_acquire_lease": ("ralph.git.git_command_queue", "try_acquire_lease"),
    "uninstall_hooks": ("ralph.git.hooks", "uninstall_hooks"),
}

_LAZY_MODULE_EXPORTS: dict[str, str] = {
    "lock_recovery": "ralph.git.lock_recovery",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_MODULE_EXPORTS.get(name)
    if module_name is not None:
        module = import_module(module_name)
        cast("dict[str, object]", globals())[name] = module
        return module

    try:
        defining_module, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg) from exc

    module = import_module(defining_module)
    namespace = cast("dict[str, object]", module.__dict__)
    value = namespace[attr_name]
    cast("dict[str, object]", globals())[name] = value
    return value
