"""Unit tests for the terminal-success authority (Authority 7)."""

from __future__ import annotations

from ralph.pipeline._terminal_authority import is_terminal_success_eligible
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.pipeline.worker_state import WorkerState, WorkerStatus


def test_terminal_success_false_when_record_present() -> None:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
    )
    assert is_terminal_success_eligible(None, record, RebaseState()) is False


def test_terminal_success_false_when_integration_unresolved() -> None:
    rebase_state = RebaseState(last_action="conflict")
    assert is_terminal_success_eligible(None, None, rebase_state) is False


def test_terminal_success_false_when_publication_not_terminal() -> None:
    rebase_state = RebaseState(last_publication_decision="publish_now")
    assert is_terminal_success_eligible(None, None, rebase_state) is False


def test_terminal_success_false_when_fan_out_pending() -> None:
    rebase_state = RebaseState(
        last_publication_decision="publication_terminal",
        last_fan_out_decision="join_pending",
    )
    assert is_terminal_success_eligible(None, None, rebase_state) is False


def test_terminal_success_false_when_fan_out_blocked() -> None:
    rebase_state = RebaseState(
        last_publication_decision="publication_terminal",
        last_fan_out_decision="join_blocked",
    )
    assert is_terminal_success_eligible(None, None, rebase_state) is False


def test_terminal_success_true_when_all_clear() -> None:
    rebase_state = RebaseState(
        last_publication_decision="publication_terminal",
        last_fan_out_decision="join_complete",
    )
    assert is_terminal_success_eligible(None, None, rebase_state) is True


def test_terminal_success_true_when_publication_none() -> None:
    """A None publication decision is treated as terminal when nothing else is open."""
    rebase_state = RebaseState(last_fan_out_decision="join_complete")
    assert is_terminal_success_eligible(None, None, rebase_state) is True


def _state_with_worker(status: WorkerStatus) -> PipelineState:
    """A complete-phase state carrying one worker in the given status."""
    worker = WorkerState(unit_id="unit-1", status=status)
    return PipelineState(phase="complete", worker_states={"unit-1": worker})


def test_terminal_success_false_when_state_has_running_worker_and_stale_cached_projection() -> None:
    """A stale join_complete cache must not report success over RUNNING workers."""
    state = _state_with_worker(WorkerStatus.RUNNING)
    rebase_state = RebaseState(
        last_publication_decision="publication_terminal",
        last_fan_out_decision="join_complete",
    )
    assert is_terminal_success_eligible(state, None, rebase_state) is False


def test_terminal_success_false_when_state_has_running_worker_and_no_cached_projection() -> None:
    """An absent cached projection with live RUNNING workers must block."""
    state = _state_with_worker(WorkerStatus.RUNNING)
    rebase_state = RebaseState(last_publication_decision="publication_terminal")
    assert is_terminal_success_eligible(state, None, rebase_state) is False


def test_terminal_success_true_when_state_agrees_with_join_complete_cache() -> None:
    state = _state_with_worker(WorkerStatus.SUCCEEDED)
    rebase_state = RebaseState(
        last_publication_decision="publication_terminal",
        last_fan_out_decision="join_complete",
    )
    assert is_terminal_success_eligible(state, None, rebase_state) is True


def test_terminal_success_false_when_state_contradicts_join_complete_cache() -> None:
    state = _state_with_worker(WorkerStatus.FAILED)
    rebase_state = RebaseState(
        last_publication_decision="publication_terminal",
        last_fan_out_decision="join_complete",
    )
    assert is_terminal_success_eligible(state, None, rebase_state) is False


def test_terminal_success_false_for_checkpoint_round_tripped_contradictory_state() -> None:
    """The contradiction must survive a checkpoint JSON round trip."""
    state = _state_with_worker(WorkerStatus.RUNNING)
    rebase_state = RebaseState(
        last_publication_decision="publication_terminal",
        last_fan_out_decision="join_complete",
    )
    state = state.copy_with(rebase=rebase_state)
    restored = PipelineState.model_validate_json(state.model_dump_json())
    assert is_terminal_success_eligible(restored, None, restored.rebase) is False
