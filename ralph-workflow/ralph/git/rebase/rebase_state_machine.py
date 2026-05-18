"""High-level rebase state machine for Python agents."""

from __future__ import annotations

from ralph.git.rebase._invalid_transition_error import InvalidTransitionError
from ralph.git.rebase._rebase_event import RebaseEvent
from ralph.git.rebase._rebase_lock import RebaseLock
from ralph.git.rebase._rebase_phase import RebasePhase
from ralph.git.rebase._recovery_action import RecoveryAction

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

__all__ = [
    "InvalidTransitionError",
    "RebaseCheckpoint",
    "RebaseEvent",
    "RebaseLock",
    "RebasePhase",
    "RebaseStateMachine",
    "RecoveryAction",
    "acquire_rebase_lock",
    "clear_rebase_checkpoint",
    "load_rebase_checkpoint",
    "rebase_checkpoint_exists",
    "release_rebase_lock",
    "restore_from_backup",
    "save_rebase_checkpoint",
]

DEFAULT_MAX_RECOVERY_ATTEMPTS = 3


class RebaseStateMachine:
    """State machine that coordinates rebase lifecycle via ``RebaseCheckpoint``."""

    def __init__(
        self,
        checkpoint: RebaseCheckpoint,
        *,
        persist: bool = True,
        max_recovery_attempts: int = DEFAULT_MAX_RECOVERY_ATTEMPTS,
    ) -> None:
        self.checkpoint = checkpoint
        self.persist = persist
        self.max_recovery_attempts = max_recovery_attempts

    @property
    def phase(self) -> RebasePhase:
        return self.checkpoint.phase

    @classmethod
    def new(
        cls,
        upstream_branch: str,
        *,
        persist: bool = True,
        max_recovery_attempts: int = DEFAULT_MAX_RECOVERY_ATTEMPTS,
    ) -> RebaseStateMachine:
        checkpoint = RebaseCheckpoint.new(upstream_branch)
        if persist:
            save_rebase_checkpoint(checkpoint)
        return cls(checkpoint, persist=persist, max_recovery_attempts=max_recovery_attempts)

    @classmethod
    def load_or_create(
        cls,
        upstream_branch: str,
        *,
        persist: bool = True,
        max_recovery_attempts: int = DEFAULT_MAX_RECOVERY_ATTEMPTS,
    ) -> RebaseStateMachine:
        checkpoint: RebaseCheckpoint | None = None
        if rebase_checkpoint_exists():
            try:
                checkpoint = load_rebase_checkpoint()
            except (OSError, ValueError):
                checkpoint = restore_from_backup()
                if checkpoint is None:
                    clear_rebase_checkpoint()
        if checkpoint is None:
            checkpoint = RebaseCheckpoint.new(upstream_branch)
        return cls(
            checkpoint,
            persist=persist,
            max_recovery_attempts=max_recovery_attempts,
        )

    def transition_to_phase(self, phase: RebasePhase) -> None:
        self.checkpoint.set_phase(phase)
        if self.persist:
            save_rebase_checkpoint(self.checkpoint)

    def start_rebase(self) -> None:
        if self.phase != RebasePhase.NotStarted:
            raise InvalidTransitionError("Rebase already started")
        self.transition_to_phase(RebasePhase.RebaseInProgress)

    def detect_conflict(self, file: str) -> None:
        if self.phase not in {
            RebasePhase.RebaseInProgress,
            RebasePhase.ConflictDetected,
            RebasePhase.ConflictResolutionInProgress,
        }:
            raise InvalidTransitionError("Cannot detect conflict from current phase")
        self.checkpoint.add_conflicted_file(file)
        self.transition_to_phase(RebasePhase.ConflictDetected)

    def start_conflict_resolution(self) -> None:
        if self.phase != RebasePhase.ConflictDetected:
            raise InvalidTransitionError("Cannot start conflict resolution now")
        self.transition_to_phase(RebasePhase.ConflictResolutionInProgress)

    def resolve_conflict(self, file: str) -> None:
        if self.phase not in {
            RebasePhase.ConflictDetected,
            RebasePhase.ConflictResolutionInProgress,
        }:
            raise InvalidTransitionError("Cannot resolve conflict now")
        if file not in self.checkpoint.conflicted_files:
            raise InvalidTransitionError("Unknown conflict file")
        self.checkpoint.add_resolved_file(file)
        if self.persist:
            save_rebase_checkpoint(self.checkpoint)

    def continue_rebase(self) -> None:
        if self.phase != RebasePhase.ConflictResolutionInProgress:
            raise InvalidTransitionError("Cannot continue until resolution is active")
        if not self.checkpoint.all_conflicts_resolved():
            raise InvalidTransitionError("Conflicts remain unresolved")
        self.transition_to_phase(RebasePhase.CompletingRebase)

    def complete_rebase(self) -> None:
        if self.phase != RebasePhase.CompletingRebase:
            raise InvalidTransitionError("Rebase is not in the completing phase")
        self.transition_to_phase(RebasePhase.RebaseComplete)

    def abort_rebase(self) -> None:
        if self.phase in {RebasePhase.RebaseComplete, RebasePhase.RebaseAborted}:
            raise InvalidTransitionError("Rebase already finished")
        self.transition_to_phase(RebasePhase.RebaseAborted)

    def record_error(self, error: str) -> None:
        self.checkpoint.record_error(error)
        if self.persist:
            save_rebase_checkpoint(self.checkpoint)

    def can_recover(self) -> bool:
        limit = self.max_recovery_attempts
        return self.checkpoint.phase_error_count < limit

    def should_abort(self) -> bool:
        limit = self.max_recovery_attempts
        return self.checkpoint.phase_error_count >= limit

    def unresolved_conflict_count(self) -> int:
        return self.checkpoint.unresolved_conflict_count()

    def all_conflicts_resolved(self) -> bool:
        return self.checkpoint.all_conflicts_resolved()

    def upstream_branch(self) -> str:
        return self.checkpoint.upstream_branch

    def clear_checkpoint(self) -> None:
        clear_rebase_checkpoint()
        self.checkpoint = RebaseCheckpoint.new(self.checkpoint.upstream_branch)
        if self.persist:
            save_rebase_checkpoint(self.checkpoint)

    def apply_event(self, event: RebaseEvent, *, file: str | None = None) -> None:
        if event == RebaseEvent.START_REBASE:
            self.start_rebase()
        elif event == RebaseEvent.CONFLICT_DETECTED:
            if file is None:
                raise InvalidTransitionError("Conflict event requires a file")
            self.detect_conflict(file)
        elif event == RebaseEvent.START_RESOLUTION:
            self.start_conflict_resolution()
        elif event == RebaseEvent.RESOLVE_CONFLICT:
            if file is None:
                raise InvalidTransitionError("Resolve event requires a file")
            self.resolve_conflict(file)
        elif event == RebaseEvent.CONTINUE:
            self.continue_rebase()
        elif event == RebaseEvent.COMPLETE:
            self.complete_rebase()
        elif event == RebaseEvent.ABORT:
            self.abort_rebase()
        else:
            raise InvalidTransitionError("Unknown event")

