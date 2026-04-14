"""Rebase-specific helpers for git operations."""

from __future__ import annotations

from .rebase_kinds import RebaseErrorKind, RebaseKind, classify_rebase_error
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
from .rebase_checkpoint import (
    RebaseCheckpoint,
    RebasePhase,
    RebaseLock,
    acquire_rebase_lock,
    clear_rebase_checkpoint,
    load_rebase_checkpoint,
    release_rebase_lock,
    restore_from_backup,
    rebase_checkpoint_exists,
    save_rebase_checkpoint,
)
from .rebase_state_machine import (
    RebaseEvent,
    RebaseStateMachine,
    RecoveryAction,
)

__all__ = [
    "RebaseErrorKind",
    "RebaseKind",
    "classify_rebase_error",
    "ConflictRemainingError",
    "NoRebaseInProgressError",
    "RebaseContinuationError",
    "RebaseVerificationError",
    "continue_rebase",
    "continue_rebase_at",
    "rebase_in_progress",
    "rebase_in_progress_at",
    "verify_rebase_completed",
    "verify_rebase_completed_at",
    "RebaseCheckpoint",
    "RebaseEvent",
    "RebaseLock",
    "RebasePhase",
    "RebaseStateMachine",
    "RecoveryAction",
    "acquire_rebase_lock",
    "clear_rebase_checkpoint",
    "load_rebase_checkpoint",
    "release_rebase_lock",
    "restore_from_backup",
    "rebase_checkpoint_exists",
    "save_rebase_checkpoint",
]
