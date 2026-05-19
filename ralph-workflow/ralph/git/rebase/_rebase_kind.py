"""RebaseKind enum describing every supported git rebase failure mode."""

from __future__ import annotations

from enum import Enum


class RebaseKind(Enum):
    """Enum describing every supported rebase failure mode."""

    INVALID_REVISION = "invalid_revision"
    DIRTY_WORKING_TREE = "dirty_working_tree"
    CONCURRENT_OPERATION = "concurrent_operation"
    REPOSITORY_CORRUPT = "repository_corrupt"
    ENVIRONMENT_FAILURE = "environment_failure"
    HOOK_REJECTION = "hook_rejection"
    CONTENT_CONFLICT = "content_conflict"
    PATCH_APPLICATION_FAILED = "patch_application_failed"
    INTERACTIVE_STOP = "interactive_stop"
    EMPTY_COMMIT = "empty_commit"
    AUTOSTASH_FAILED = "autostash_failed"
    COMMIT_CREATION_FAILED = "commit_creation_failed"
    REFERENCE_UPDATE_FAILED = "reference_update_failed"
    UNKNOWN = "unknown"


__all__ = ["RebaseKind"]
