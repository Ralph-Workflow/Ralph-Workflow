from __future__ import annotations

import pytest

from ralph.git.rebase.rebase_state_machine import (
    InvalidTransitionError,
    RebasePhase,
    RebaseStateMachine,
)


def test_start_rebase_transitions_from_idle_to_in_progress() -> None:
    machine = RebaseStateMachine.new("main")
    assert machine.phase == RebasePhase.NotStarted
    machine.start_rebase()
    assert machine.phase == RebasePhase.RebaseInProgress


def test_conflict_detection_records_conflict_and_moves_to_conflict_state() -> None:
    machine = RebaseStateMachine.new("main")
    machine.start_rebase()
    machine.detect_conflict("file.py")
    assert machine.phase == RebasePhase.ConflictDetected
    assert machine.unresolved_conflict_count() == 1


def test_conflict_resolution_allows_continue_and_completion() -> None:
    machine = RebaseStateMachine.new("main")
    machine.start_rebase()
    machine.detect_conflict("file.py")

    with pytest.raises(InvalidTransitionError):
        machine.continue_rebase()

    machine.start_conflict_resolution()
    machine.resolve_conflict("file.py")
    assert machine.all_conflicts_resolved()

    machine.continue_rebase()
    assert machine.phase == RebasePhase.CompletingRebase

    machine.complete_rebase()
    assert machine.phase == RebasePhase.RebaseComplete


def test_abort_from_active_phase_moves_to_aborted() -> None:
    machine = RebaseStateMachine.new("main")
    machine.start_rebase()
    machine.detect_conflict("file.py")
    machine.abort_rebase()
    assert machine.phase == RebasePhase.RebaseAborted


def test_invalid_transition_without_conflict_raises_error() -> None:
    machine = RebaseStateMachine.new("main")

    with pytest.raises(InvalidTransitionError):
        machine.resolve_conflict("file.py")


def test_error_tracking_does_not_allow_infinite_retries() -> None:
    machine = RebaseStateMachine.new("main")
    for attempt in range(machine.max_recovery_attempts):
        machine.record_error(f"error {attempt}")

    assert machine.should_abort()
    assert not machine.can_recover()
