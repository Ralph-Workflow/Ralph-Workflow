"""Unit tests for the pure pipeline reducer."""

from __future__ import annotations

from ralph.config.enums import (
    PHASE_COMPLETE,
    PHASE_DEVELOPMENT,
    PHASE_FAILED,
    PHASE_FIX,
    PHASE_REVIEW,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState


def test_agent_success_advances_iteration() -> None:
    """Test that AGENT_SUCCESS in development advances iteration."""
    state = PipelineState(
        total_iterations=3,
        iteration=0,
        phase=PHASE_DEVELOPMENT,
    )
    new_state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS)
    assert new_state.iteration == 1
    assert new_state.phase == PHASE_DEVELOPMENT


def test_final_iteration_advances_to_review() -> None:
    """Test that AGENT_SUCCESS on final iteration advances to review."""
    state = PipelineState(
        total_iterations=2,
        iteration=1,
        phase=PHASE_DEVELOPMENT,
    )
    new_state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS)
    assert new_state.phase == PHASE_REVIEW


def test_review_clean_advances_to_commit() -> None:
    """Test that REVIEW_CLEAN advances to commit phase."""
    state = PipelineState(phase=PHASE_REVIEW)
    new_state, _ = reducer_reduce(state, PipelineEvent.REVIEW_CLEAN)
    assert new_state.phase == PHASE_REVIEW


def test_review_issues_found_advances_to_fix() -> None:
    """Test that REVIEW_ISSUES_FOUND advances to fix phase."""
    state = PipelineState(
        phase=PHASE_REVIEW,
        reviewer_pass=0,
        total_reviewer_passes=2,
    )
    new_state, _ = reducer_reduce(state, PipelineEvent.REVIEW_ISSUES_FOUND)
    assert new_state.phase == PHASE_FIX
    assert new_state.reviewer_pass == 1


def test_fix_success_returns_to_review() -> None:
    """Test that FIX_SUCCESS returns to review phase."""
    state = PipelineState(
        phase=PHASE_FIX,
        reviewer_pass=0,
        total_reviewer_passes=2,
    )
    new_state, _ = reducer_reduce(state, PipelineEvent.FIX_SUCCESS)
    assert new_state.phase == PHASE_REVIEW


def test_commit_success_advances_to_complete() -> None:
    """Test that COMMIT_SUCCESS advances to complete phase."""
    state = PipelineState(phase=PHASE_DEVELOPMENT)
    new_state, _ = reducer_reduce(state, PipelineEvent.COMMIT_SUCCESS)
    assert new_state.phase == PHASE_COMPLETE


def test_agent_failure_triggers_retry() -> None:
    """Test that AGENT_FAILURE increments retry count."""
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=0),
    )
    new_state, _ = reducer_reduce(state, PipelineEvent.AGENT_FAILURE)
    assert new_state.dev_chain.retries == 1


def test_agent_failure_falls_back_to_next_agent() -> None:
    """Test that AGENT_FAILURE falls back to next agent after max retries."""
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=["claude", "opencode"], current_index=0, retries=3),
    )
    new_state, _ = reducer_reduce(state, PipelineEvent.AGENT_FAILURE)
    assert new_state.dev_chain.current_index == 1
    assert new_state.dev_chain.retries == 0


def test_agent_failure_with_exhausted_chain_fails() -> None:
    """Test that AGENT_FAILURE with exhausted chain transitions to failed."""
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=3),
    )
    new_state, _ = reducer_reduce(state, PipelineEvent.AGENT_FAILURE)
    assert new_state.phase == PHASE_FAILED


def test_checkpoint_saved_increments_count() -> None:
    """Test that CHECKPOINT_SAVED increments the checkpoint counter."""
    state = PipelineState(checkpoint_saved_count=0)
    new_state, _ = reducer_reduce(state, PipelineEvent.CHECKPOINT_SAVED)
    assert new_state.checkpoint_saved_count == 1


def test_interrupted_sets_flag() -> None:
    """Test that INTERRUPTED sets the interrupted_by_user flag."""
    state = PipelineState(interrupted_by_user=False)
    new_state, _ = reducer_reduce(state, PipelineEvent.INTERRUPTED)
    assert new_state.interrupted_by_user is True


def test_is_complete_returns_true_for_complete() -> None:
    """Test that is_complete() returns True for COMPLETE phase."""
    state = PipelineState(phase=PHASE_COMPLETE)
    assert state.is_complete() is True


def test_is_complete_returns_true_for_failed() -> None:
    """Test that is_complete() returns True for FAILED phase."""
    state = PipelineState(phase=PHASE_FAILED)
    assert state.is_complete() is True


def test_is_complete_returns_false_for_development() -> None:
    """Test that is_complete() returns False for DEVELOPMENT phase."""
    state = PipelineState(phase=PHASE_DEVELOPMENT)
    assert state.is_complete() is False
