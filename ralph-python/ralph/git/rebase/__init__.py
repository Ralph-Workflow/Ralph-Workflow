"""Rebase-specific helpers for git operations."""

from __future__ import annotations

from .rebase_checkpoint import (
    RebaseCheckpoint,
    RebaseLock,
    RebasePhase,
    acquire_rebase_lock,
    clear_rebase_checkpoint,
    load_rebase_checkpoint,
    rebase_checkpoint_exists,
    release_rebase_lock,
    restore_from_backup,
    save_rebase_checkpoint,
)
from .rebase_continuation import (
    ConflictRemainingError,
    NoRebaseInProgressError,
    RebaseContinuationError,
    RebaseVerificationError,
    continue_rebase,
    continue_rebase_at,
    rebase_in_progress,
    rebase_in_progress_at,
    verify_rebase_completed,
    verify_rebase_completed_at,
)
from .rebase_kinds import RebaseErrorKind, RebaseKind, classify_rebase_error
from .rebase_preconditions import (
    RebasePreconditionError,
    check_rebase_preconditions,
)
from .rebase_state_machine import (
    RebaseEvent,
    RebaseStateMachine,
    RecoveryAction,
)

__all__ = [
    "ConflictRemainingError",
    "NoRebaseInProgressError",
    "RebaseCheckpoint",
    "RebaseContinuationError",
    "RebaseErrorKind",
    "RebaseEvent",
    "RebaseKind",
    "RebaseLock",
    "RebasePhase",
    "RebasePreconditionError",
    "RebaseStateMachine",
    "RebaseVerificationError",
    "RecoveryAction",
    "acquire_rebase_lock",
    "check_rebase_preconditions",
    "classify_rebase_error",
    "clear_rebase_checkpoint",
    "continue_rebase",
    "continue_rebase_at",
    "load_rebase_checkpoint",
    "rebase_checkpoint_exists",
    "rebase_in_progress",
    "rebase_in_progress_at",
    "release_rebase_lock",
    "restore_from_backup",
    "save_rebase_checkpoint",
    "verify_rebase_completed",
    "verify_rebase_completed_at",
]
