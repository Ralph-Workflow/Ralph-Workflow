"""Rebase-specific helpers for git operations."""

from __future__ import annotations

from ralph.git.rebase._rebase_lock import RebaseLock
from ralph.git.rebase._rebase_phase import RebasePhase

from .rebase import (
    RebaseConflicts,
    RebaseFailed,
    RebaseNoOp,
    RebaseResult,
    RebaseSuccess,
    abort_rebase,
    get_conflicted_files,
    rebase_onto,
)
from .rebase_checkpoint import (
    RebaseCheckpoint,
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

# NOTE: ``rebase_in_progress`` is imported from ``.rebase_continuation``
# (above). It is intentionally NOT re-exported from ``.rebase`` because
# ``.rebase`` ALSO defines a private ``rebase_in_progress`` (used by
# ``abort_rebase``/``continue_rebase``), and importing the same name
# from both modules would produce a ruff F811 redefinition that fails
# ``make verify``. The auto-integrate consumer imports
# ``rebase_in_progress`` directly from ``ralph.git.rebase.rebase`` (the
# repo-root-explicit module) when it needs the local-resolving variant
# and from ``ralph.git.rebase.rebase_continuation`` for the
# auto-detecting variant.

__all__ = [
    "ConflictRemainingError",
    "NoRebaseInProgressError",
    "RebaseCheckpoint",
    "RebaseConflicts",
    "RebaseContinuationError",
    "RebaseErrorKind",
    "RebaseEvent",
    "RebaseFailed",
    "RebaseKind",
    "RebaseLock",
    "RebaseNoOp",
    "RebasePhase",
    "RebasePreconditionError",
    "RebaseResult",
    "RebaseStateMachine",
    "RebaseSuccess",
    "RebaseVerificationError",
    "RecoveryAction",
    "abort_rebase",
    "acquire_rebase_lock",
    "check_rebase_preconditions",
    "classify_rebase_error",
    "clear_rebase_checkpoint",
    "continue_rebase",
    "continue_rebase_at",
    "get_conflicted_files",
    "load_rebase_checkpoint",
    "rebase_checkpoint_exists",
    "rebase_in_progress",
    "rebase_in_progress_at",
    "rebase_onto",
    "release_rebase_lock",
    "restore_from_backup",
    "save_rebase_checkpoint",
    "verify_rebase_completed",
    "verify_rebase_completed_at",
]
