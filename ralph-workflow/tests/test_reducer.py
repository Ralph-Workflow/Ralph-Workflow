"""Unit tests for the pure pipeline reducer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, patch

import pytest

from ralph.config.enums import (
    PHASE_COMPLETE,
    PHASE_DEVELOPMENT,
    PHASE_DEVELOPMENT_ANALYSIS,
    PHASE_FAILED,
    PHASE_FIX,
    PHASE_REVIEW,
    PHASE_REVIEW_ANALYSIS,
    PHASE_REVIEW_COMMIT,
    PipelinePhase,
)
from ralph.pipeline.events import (
    PhaseFailureEvent,
    PipelineEvent,
    WorkerCompletedEvent,
    WorkerFailedEvent,
    WorkerStartedEvent,
)
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, CommitState, PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus
from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PostCommitRoute,
    PostCommitRouteWhen,
)

if TYPE_CHECKING:
    from ralph.pipeline.effects import Effect


def _reduce(
    state: PipelineState,
    event: object,
    policy: PipelinePolicy | None = None,
) -> tuple[PipelineState, list[Effect]]:
    return reducer_reduce(state, cast("Any", event), policy)


def _basic_pipeline_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            PHASE_DEVELOPMENT: PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success=PHASE_COMPLETE,
                    on_failure=PHASE_FAILED,
                    on_loopback=PHASE_DEVELOPMENT,
                ),
            ),
        },
        entry_phase=PHASE_DEVELOPMENT,
        terminal_phase=PHASE_COMPLETE,
    )


def _policy_with_transition(target_phase: PipelinePhase) -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "budget_transition": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success=target_phase,
                    on_failure=PHASE_FAILED,
                    on_loopback=PHASE_DEVELOPMENT,
                ),
            ),
            PHASE_DEVELOPMENT: PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success=PHASE_COMPLETE,
                    on_failure=PHASE_FAILED,
                    on_loopback=PHASE_DEVELOPMENT,
                ),
            ),
            PHASE_REVIEW: PhaseDefinition(
                drain="review",
                transitions=PhaseTransition(
                    on_success=PHASE_COMPLETE,
                    on_failure=PHASE_FAILED,
                    on_loopback=PHASE_REVIEW,
                ),
            ),
        },
        entry_phase="budget_transition",
        terminal_phase=PHASE_COMPLETE,
    )


def _policy_with_post_commit_routes() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                transitions=PhaseTransition(on_success=PHASE_DEVELOPMENT),
            ),
            PHASE_DEVELOPMENT: PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="development_analysis"),
            ),
            "development_analysis": PhaseDefinition(
                drain="development_analysis",
                transitions=PhaseTransition(
                    on_success="development_commit",
                    on_loopback=PHASE_DEVELOPMENT,
                ),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                transitions=PhaseTransition(on_success=PHASE_REVIEW),
            ),
            PHASE_REVIEW: PhaseDefinition(
                drain="review",
                transitions=PhaseTransition(on_success="review_analysis", on_loopback=PHASE_FIX),
            ),
            "review_analysis": PhaseDefinition(
                drain="review_analysis",
                transitions=PhaseTransition(on_success="review_commit", on_loopback=PHASE_FIX),
            ),
            PHASE_FIX: PhaseDefinition(
                drain="fix",
                transitions=PhaseTransition(
                    on_success=PHASE_REVIEW_ANALYSIS,
                    on_loopback=PHASE_REVIEW,
                ),
            ),
            "review_commit": PhaseDefinition(
                drain="review_commit",
                transitions=PhaseTransition(on_success=PHASE_COMPLETE),
            ),
            PHASE_COMPLETE: PhaseDefinition(
                drain="complete",
                transitions=PhaseTransition(on_success=PHASE_COMPLETE, on_loopback=PHASE_COMPLETE),
            ),
        },
        entry_phase="planning",
        terminal_phase=PHASE_COMPLETE,
        post_commit_routes=[
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="development_commit", budget_state="remaining"),
                target="planning",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="development_commit", budget_state="exhausted"),
                target=PHASE_REVIEW,
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="review_commit", budget_state="remaining"),
                target=PHASE_REVIEW,
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="review_commit", budget_state="exhausted"),
                target=PHASE_COMPLETE,
            ),
        ],
    )


# =============================================================================
# PhaseFailureEvent tests
# =============================================================================


class TestPhaseFailureEvent:
    """Tests for PhaseFailureEvent routing through the reducer."""

    def test_phase_failure_recoverable_increments_retries(self) -> None:
        """PhaseFailureEvent(recoverable=True) increments retry count."""
        state = PipelineState(
            phase=PHASE_DEVELOPMENT,
            dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=0),
        )
        event = PhaseFailureEvent(phase="development", reason="missing artifact", recoverable=True)
        new_state, effects = _reduce(state, event)
        assert new_state.dev_chain.retries == 1
        assert new_state.phase == PHASE_DEVELOPMENT
        assert effects == []

    def test_phase_failure_recoverable_after_3_retries_falls_back_to_next_agent(
        self,
    ) -> None:
        """After 3 retries, recoverable PhaseFailureEvent advances to next agent."""
        state = PipelineState(
            phase=PHASE_DEVELOPMENT,
            dev_chain=AgentChainState(agents=["claude", "opencode"], current_index=0, retries=3),
        )
        event = PhaseFailureEvent(phase="development", reason="missing artifact", recoverable=True)
        new_state, effects = _reduce(state, event)
        assert new_state.dev_chain.current_index == 1
        assert new_state.dev_chain.retries == 0
        assert effects == []

    def test_phase_failure_recoverable_with_single_agent_after_3_retries_enters_recovery(
        self,
    ) -> None:
        """Single-agent chain exhaustion should enter recovery without exit effects."""
        state = PipelineState(
            phase=PHASE_DEVELOPMENT,
            dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=3),
        )
        event = PhaseFailureEvent(phase="development", reason="missing artifact", recoverable=True)
        new_state, effects = _reduce(state, event)
        assert new_state.phase == PHASE_FAILED
        assert new_state.last_error is not None
        assert "development" in new_state.last_error
        assert "missing artifact" in new_state.last_error
        assert new_state.recovery_epoch == 1
        assert effects == []

    def test_phase_failure_not_recoverable_enters_recovery_without_exit_effect(
        self,
    ) -> None:
        """PhaseFailureEvent(recoverable=False) should still avoid process exit."""
        state = PipelineState(
            phase=PHASE_DEVELOPMENT,
            dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=0),
        )
        event = PhaseFailureEvent(
            phase="development_analysis",
            reason="Analysis decision: FAILURE",
            recoverable=False,
        )
        new_state, effects = _reduce(state, event)
        assert new_state.phase == PHASE_FAILED
        assert new_state.last_error == "development_analysis: Analysis decision: FAILURE"
        assert new_state.recovery_epoch == 1
        assert effects == []

    def test_phase_failure_recoverable_preserves_reason_in_last_error(self) -> None:
        """When chain exhausts, the original PhaseFailureEvent reason is preserved."""
        state = PipelineState(
            phase=PHASE_DEVELOPMENT,
            dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=3),
        )
        event = PhaseFailureEvent(
            phase="development",
            reason="Invalid development evidence: missing planning artifact",
            recoverable=True,
        )
        new_state, _effects = _reduce(state, event)
        assert new_state.phase == PHASE_FAILED
        assert new_state.last_error is not None
        assert "missing planning artifact" in new_state.last_error
        assert "development" in new_state.last_error
        assert new_state.recovery_epoch == 1

    def test_phase_failure_never_produces_unknown_failure_string(self) -> None:
        """Terminal failure from PhaseFailureEvent must never show 'Unknown failure'."""
        state = PipelineState(
            phase=PHASE_REVIEW,
            rev_chain=AgentChainState(agents=["reviewer"], current_index=0, retries=3),
        )
        event = PhaseFailureEvent(
            phase="review",
            reason="Missing/invalid issues artifact",
            recoverable=True,
        )
        new_state, effects = _reduce(state, event)
        assert new_state.phase == PHASE_FAILED
        assert new_state.last_error is not None
        assert new_state.last_error != "Unknown failure"
        assert "Unknown failure" not in new_state.last_error
        assert new_state.recovery_epoch == 1
        assert effects == []


# =============================================================================
# Existing reducer tests (unchanged)
# =============================================================================


def test_policy_agent_success_in_development_routes_to_analysis() -> None:
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase=PHASE_DEVELOPMENT)
    new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
    assert new_state.phase == "development_analysis"
    assert new_state.previous_phase == PHASE_DEVELOPMENT


def test_policy_agent_success_in_planning_routes_to_development_and_decrements_budget() -> None:
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase="planning", development_budget_remaining=2)
    new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
    assert new_state.phase == PHASE_DEVELOPMENT
    assert new_state.development_budget_remaining == 1


def test_policy_agent_success_with_embeds_analysis_delegates_to_analysis_path() -> None:
    policy = PipelinePolicy(
        phases={
            PHASE_DEVELOPMENT: PhaseDefinition(
                drain="development",
                embeds_analysis=True,
                transitions=PhaseTransition(
                    on_success="development_commit",
                    on_loopback=PHASE_DEVELOPMENT,
                ),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                transitions=PhaseTransition(on_success=PHASE_COMPLETE),
            ),
            PHASE_COMPLETE: PhaseDefinition(
                drain="complete",
                transitions=PhaseTransition(on_success=PHASE_COMPLETE, on_loopback=PHASE_COMPLETE),
            ),
        },
        entry_phase=PHASE_DEVELOPMENT,
        terminal_phase=PHASE_COMPLETE,
    )
    state = PipelineState(phase=PHASE_DEVELOPMENT)
    new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
    assert new_state.phase == "development_commit"
    assert new_state.previous_phase == PHASE_DEVELOPMENT


def test_review_clean_advances_to_commit() -> None:
    """Test that REVIEW_CLEAN advances to commit phase."""
    state = PipelineState(phase=PHASE_REVIEW)
    new_state, _ = _reduce(state, PipelineEvent.REVIEW_CLEAN)
    assert new_state.phase == PHASE_REVIEW_COMMIT


def test_review_issues_found_advances_to_fix_without_completing_pass() -> None:
    """REVIEW_ISSUES_FOUND should route to fix without incrementing reviewer_pass."""
    state = PipelineState(
        phase=PHASE_REVIEW,
        reviewer_pass=0,
        total_reviewer_passes=2,
    )
    new_state, _ = _reduce(state, PipelineEvent.REVIEW_ISSUES_FOUND)
    assert new_state.phase == PHASE_FIX
    assert new_state.reviewer_pass == 0
    assert new_state.review_issues_found is True


def test_fix_success_returns_to_review_analysis() -> None:
    """Test that FIX_SUCCESS returns to review_analysis phase for verification."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(
        phase=PHASE_FIX,
        reviewer_pass=0,
        total_reviewer_passes=2,
    )
    new_state, _ = _reduce(state, PipelineEvent.FIX_SUCCESS, policy)
    assert new_state.phase == PHASE_REVIEW_ANALYSIS


def test_commit_success_advances_to_complete() -> None:
    """Test that COMMIT_SUCCESS advances to complete phase."""
    state = PipelineState(phase=PHASE_DEVELOPMENT)
    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS)
    assert new_state.phase == PHASE_COMPLETE


def test_policy_agent_success_unknown_phase_routes_to_failed() -> None:
    """Policy-driven agent success should fail when phase is missing from policy."""
    state = PipelineState(phase="missing")
    policy = _basic_pipeline_policy()

    new_state, effects = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)

    assert new_state.phase == PHASE_FAILED
    assert new_state.previous_phase == "missing"
    assert new_state.last_error == "Unknown phase: missing"
    assert new_state.recovery_epoch == 1
    assert effects == []


def test_phase_advance_event_fails_on_unknown_policy_phase() -> None:
    """PHASE_ADVANCE should fail with a routing error when the phase is missing from the policy."""
    state = PipelineState(phase="missing")
    policy = _basic_pipeline_policy()

    new_state, effects = _reduce(state, PipelineEvent.PHASE_ADVANCE, policy)

    assert new_state.phase == PHASE_FAILED
    assert new_state.previous_phase == "missing"
    assert new_state.recovery_epoch == 1
    assert "missing" in (new_state.last_error or "")
    assert effects == []


def test_fix_failure_policy_terminal_transition_emits_exit_failure() -> None:
    """FIX_FAILURE should fail the pipeline when the policy points to a terminal phase."""
    policy = PipelinePolicy(
        phases={
            PHASE_FIX: PhaseDefinition(
                drain="fix",
                transitions=PhaseTransition(
                    on_success=PHASE_COMPLETE,
                    on_failure=PHASE_FAILED,
                    on_loopback=PHASE_FIX,
                ),
            ),
        },
        entry_phase=PHASE_FIX,
        terminal_phase=PHASE_COMPLETE,
    )
    state = PipelineState(phase=PHASE_FIX)

    new_state, effects = _reduce(state, PipelineEvent.FIX_FAILURE, policy)

    assert new_state.phase == PHASE_FAILED
    assert new_state.previous_phase == PHASE_FIX
    assert new_state.recovery_epoch == 1
    assert effects == []


def test_phase_advance_decreases_development_budget() -> None:
    """Advancing to development should decrement the development budget."""
    policy = _policy_with_transition(PHASE_DEVELOPMENT)
    state = PipelineState(phase="budget_transition", development_budget_remaining=2)

    new_state, _ = _reduce(state, PipelineEvent.PHASE_ADVANCE, policy)

    assert new_state.phase == PHASE_DEVELOPMENT
    assert new_state.development_budget_remaining == 1


def test_phase_advance_updates_current_drain_from_target_phase_policy() -> None:
    """Advancing phases should update the authoritative current drain from policy."""
    policy = _policy_with_transition(PHASE_DEVELOPMENT)
    state = PipelineState(phase="budget_transition", current_drain="planning")

    new_state, _ = _reduce(state, PipelineEvent.PHASE_ADVANCE, policy)

    assert new_state.phase == PHASE_DEVELOPMENT
    assert new_state.current_drain == "development"


def test_phase_advance_clamps_review_budget_at_zero() -> None:
    """Review budget should never go negative when advancing to review."""
    policy = _policy_with_transition(PHASE_REVIEW)
    state = PipelineState(phase="budget_transition", review_budget_remaining=0)

    new_state, _ = _reduce(state, PipelineEvent.PHASE_ADVANCE, policy)

    assert new_state.phase == PHASE_REVIEW
    assert new_state.review_budget_remaining == 0


def test_commit_success_routes_development_commit_to_planning_when_budget_remaining() -> None:
    """COMMIT_SUCCESS should route development_commit to planning when budget remains."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase="development_commit", development_budget_remaining=1)

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert new_state.phase == "planning"
    assert new_state.previous_phase == "development_commit"
    assert new_state.current_drain == "planning"


def test_commit_success_increments_development_iteration_with_policy() -> None:
    """A completed development commit should advance the visible iteration counter."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(
        phase="development_commit",
        iteration=0,
        total_iterations=2,
        development_budget_remaining=1,
        review_budget_remaining=1,
    )

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert new_state.phase == "planning"
    assert new_state.iteration == 1


def test_commit_success_routes_development_commit_to_review_when_budget_exhausted() -> None:
    """COMMIT_SUCCESS should route development_commit to review when budget exhausted."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(
        phase="development_commit",
        development_budget_remaining=0,
        review_budget_remaining=1,
    )

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert new_state.phase == PHASE_REVIEW
    assert new_state.previous_phase == "development_commit"


def test_commit_success_routes_review_commit_to_review_when_budget_remaining() -> None:
    """COMMIT_SUCCESS should route review_commit to review when budget remains."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase="review_commit", review_budget_remaining=1)

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert new_state.phase == PHASE_REVIEW
    assert new_state.previous_phase == "review_commit"


def test_commit_success_increments_reviewer_pass_with_policy() -> None:
    """A completed review commit should advance the visible reviewer pass counter."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(
        phase="review_commit",
        reviewer_pass=0,
        total_reviewer_passes=2,
        review_budget_remaining=1,
    )

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert new_state.phase == PHASE_REVIEW
    assert new_state.reviewer_pass == 1


def test_commit_success_routes_review_commit_to_complete_when_budget_exhausted() -> None:
    """COMMIT_SUCCESS should route review_commit to complete when budget exhausted."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase="review_commit", review_budget_remaining=0)

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert new_state.phase == PHASE_COMPLETE
    assert new_state.previous_phase == "review_commit"


def test_agent_failure_triggers_retry() -> None:
    """Test that AGENT_FAILURE increments retry count."""
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=0),
    )
    new_state, _ = _reduce(state, PipelineEvent.AGENT_FAILURE)
    assert new_state.dev_chain.retries == 1


def test_agent_failure_falls_back_to_next_agent() -> None:
    """Test that AGENT_FAILURE falls back to next agent after max retries."""
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=["claude", "opencode"], current_index=0, retries=3),
    )
    new_state, _ = _reduce(state, PipelineEvent.AGENT_FAILURE)
    assert new_state.dev_chain.current_index == 1
    assert new_state.dev_chain.retries == 0


def test_agent_failure_with_exhausted_chain_enters_recovery() -> None:
    """AGENT_FAILURE with exhausted chain should enter recovery without exit effects."""
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=3),
    )
    new_state, effects = _reduce(state, PipelineEvent.AGENT_FAILURE)
    assert new_state.phase == PHASE_FAILED
    assert new_state.recovery_epoch == 1
    assert effects == []


def test_planning_agent_failure_uses_planning_chain_instead_of_review_chain() -> None:
    state = PipelineState(
        phase="planning",
        planning_chain=AgentChainState(agents=["claude", "opencode"], current_index=0, retries=0),
        rev_chain=AgentChainState(agents=["reviewer"], current_index=0, retries=0),
    )

    new_state, _ = _reduce(state, PipelineEvent.AGENT_FAILURE)

    assert new_state.planning_chain.retries == 1
    assert new_state.rev_chain.retries == 0


def test_checkpoint_saved_increments_count() -> None:
    """Test that CHECKPOINT_SAVED increments the checkpoint counter."""
    state = PipelineState(checkpoint_saved_count=0)
    new_state, _ = _reduce(state, PipelineEvent.CHECKPOINT_SAVED)
    assert new_state.checkpoint_saved_count == 1


def test_interrupted_sets_flag() -> None:
    """Test that INTERRUPTED sets the interrupted_by_user flag."""
    state = PipelineState(interrupted_by_user=False)
    new_state, _ = _reduce(state, PipelineEvent.INTERRUPTED)
    assert new_state.interrupted_by_user is True


def test_is_complete_returns_true_for_complete() -> None:
    """Test that is_complete() returns True for COMPLETE phase."""
    state = PipelineState(phase=PHASE_COMPLETE)
    assert state.is_complete() is True


def test_is_complete_returns_false_for_failed() -> None:
    """Failed phase is recoverable and must not be treated as complete."""
    state = PipelineState(phase=PHASE_FAILED)
    assert state.is_complete() is False


def test_is_complete_returns_false_for_development() -> None:
    """Test that is_complete() returns False for DEVELOPMENT phase."""
    state = PipelineState(phase=PHASE_DEVELOPMENT)
    assert state.is_complete() is False


class TestAnalysisDecisionDispatch:
    """Tests for AnalysisDecision-driven routing through the reducer.

    These tests verify that ANALYSIS_SUCCESS and ANALYSIS_LOOPBACK events
    (emitted by the phase handlers based on AnalysisDecision values)
    correctly route between development_analysis/review_analysis and their
    target phases.
    """

    def test_analysis_success_routes_development_analysis_to_commit(self) -> None:
        """Test that ANALYSIS_SUCCESS in development_analysis routes to development_commit."""
        state = PipelineState(
            phase="development_analysis",
            iteration=0,
            total_iterations=2,
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS)
        assert new_state.phase == "development_commit"
        assert new_state.previous_phase == "development_analysis"

    def test_analysis_loopback_routes_development_analysis_to_development(self) -> None:
        """Test that ANALYSIS_LOOPBACK in development_analysis routes back to development."""
        state = PipelineState(
            phase="development_analysis",
            iteration=0,
            total_iterations=2,
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK)
        assert new_state.phase == PHASE_DEVELOPMENT
        assert new_state.previous_phase == "development_analysis"

    def test_analysis_loopback_does_not_decrement_budget(self) -> None:
        """Analysis loopback re-enters development WITHOUT consuming a budget slot."""
        initial_budget = 2
        state = PipelineState(
            phase="development_analysis",
            development_budget_remaining=initial_budget,
        )
        policy = _policy_with_post_commit_routes()
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "development"
        assert new_state.development_budget_remaining == initial_budget

    def test_analysis_success_routes_review_analysis_to_commit(self) -> None:
        """Test that ANALYSIS_SUCCESS in review_analysis routes to review_commit."""
        state = PipelineState(
            phase="review_analysis",
            reviewer_pass=0,
            total_reviewer_passes=2,
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS)
        assert new_state.phase == "review_commit"
        assert new_state.previous_phase == "review_analysis"

    def test_analysis_loopback_routes_review_analysis_to_fix(self) -> None:
        """Test that ANALYSIS_LOOPBACK in review_analysis routes to fix."""
        state = PipelineState(
            phase="review_analysis",
            reviewer_pass=0,
            total_reviewer_passes=2,
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK)
        assert new_state.phase == PHASE_FIX
        assert new_state.previous_phase == "review_analysis"

    def test_analysis_loopback_with_policy_marks_review_issue_without_completing_pass(self) -> None:
        """Policy routing must preserve review bookkeeping on loopback to fix."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            reviewer_pass=0,
            total_reviewer_passes=2,
            review_issues_found=False,
        )

        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)

        assert new_state.phase == PHASE_FIX
        assert new_state.reviewer_pass == 0
        assert new_state.review_issues_found is True

    def test_analysis_success_with_policy_clears_review_issue_flag(self) -> None:
        """Policy routing should clear stale review issue state on approval."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            reviewer_pass=1,
            total_reviewer_passes=2,
            review_issues_found=True,
        )

        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)

        assert new_state.phase == "review_commit"
        assert new_state.review_issues_found is False

    def test_analysis_success_with_policy_routes_correctly(self) -> None:
        """Test that ANALYSIS_SUCCESS respects pipeline policy routing."""
        # Build a minimal pipeline policy
        policy = PipelinePolicy(
            phases={
                "development_analysis": PhaseDefinition(
                    drain="development_analysis",
                    transitions=PhaseTransition(
                        on_success="development_commit",
                        on_loopback="development",
                    ),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(
                        on_success="development_analysis",
                        on_loopback="development",
                    ),
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
                "complete": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="development_analysis",
            terminal_phase="complete",
        )

        state = PipelineState(phase="development_analysis")
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)
        assert new_state.phase == "development_commit"

    def test_analysis_loopback_with_policy_routes_correctly(self) -> None:
        """Test that ANALYSIS_LOOPBACK respects pipeline policy routing."""
        policy = PipelinePolicy(
            phases={
                "development_analysis": PhaseDefinition(
                    drain="development_analysis",
                    transitions=PhaseTransition(
                        on_success="development_commit",
                        on_loopback="development",
                    ),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(
                        on_success="development_analysis",
                        on_loopback="development",
                    ),
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
                "complete": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="development_analysis",
            terminal_phase="complete",
        )

        state = PipelineState(phase="development_analysis")
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "development"

    def test_dev_analysis_loopback_increments_dev_analysis_iteration(self) -> None:
        """ANALYSIS_LOOPBACK in development_analysis increments the iteration counter."""
        state = PipelineState(
            phase="development_analysis",
            development_analysis_iteration=0,
            max_development_analysis_iterations=3,
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK)
        assert new_state.development_analysis_iteration == 1

    def test_dev_analysis_loopback_at_max_routes_to_development(self) -> None:
        """At max iterations, ANALYSIS_LOOPBACK still routes to development."""
        state = PipelineState(
            phase="development_analysis",
            development_analysis_iteration=2,
            max_development_analysis_iterations=3,
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK)
        assert new_state.phase == PHASE_DEVELOPMENT
        assert new_state.previous_phase == "development_analysis"
        assert new_state.development_analysis_iteration == state.max_development_analysis_iterations

    def test_dev_analysis_loopback_already_at_cap_stays_clamped(self) -> None:
        """Further loopbacks after the cap should not increment beyond the cap."""
        state = PipelineState(
            phase="development_analysis",
            development_analysis_iteration=3,
            max_development_analysis_iterations=3,
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK)
        assert new_state.phase == PHASE_DEVELOPMENT
        assert new_state.previous_phase == "development_analysis"
        assert new_state.development_analysis_iteration == state.max_development_analysis_iterations

    def test_dev_analysis_loopback_with_zero_cap_stays_zero(self) -> None:
        """A zero configured cap should still route to development without incrementing."""
        state = PipelineState(
            phase="development_analysis",
            development_analysis_iteration=0,
            max_development_analysis_iterations=0,
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK)
        assert new_state.phase == PHASE_DEVELOPMENT
        assert new_state.previous_phase == "development_analysis"
        assert new_state.development_analysis_iteration == 0

    def test_dev_analysis_success_resets_dev_analysis_iteration(self) -> None:
        """ANALYSIS_SUCCESS in development_analysis resets the iteration counter."""
        state = PipelineState(
            phase="development_analysis",
            development_analysis_iteration=2,
            max_development_analysis_iterations=3,
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS)
        assert new_state.development_analysis_iteration == 0

    def test_commit_success_resets_dev_analysis_iteration(self) -> None:
        """COMMIT_SUCCESS in development_commit resets development_analysis_iteration."""
        state = PipelineState(
            phase="development_commit",
            development_analysis_iteration=2,
            max_development_analysis_iterations=3,
        )
        new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS)
        assert new_state.development_analysis_iteration == 0

    def test_review_analysis_loopback_increments_review_analysis_iteration(self) -> None:
        """ANALYSIS_LOOPBACK in review_analysis increments the iteration counter."""
        state = PipelineState(
            phase="review_analysis",
            review_analysis_iteration=0,
            max_review_analysis_iterations=3,
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK)
        assert new_state.review_analysis_iteration == 1

    def test_review_analysis_loopback_at_max_routes_to_fix(self) -> None:
        """At the review-analysis cap, ANALYSIS_LOOPBACK still routes to fix."""
        state = PipelineState(
            phase="review_analysis",
            review_analysis_iteration=2,
            max_review_analysis_iterations=3,
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK)
        assert new_state.phase == PHASE_FIX
        assert new_state.previous_phase == "review_analysis"
        assert new_state.review_analysis_iteration == state.max_review_analysis_iterations

    def test_review_analysis_loopback_already_at_cap_stays_clamped(self) -> None:
        """Further review loopbacks after the cap should not increment beyond the cap."""
        state = PipelineState(
            phase="review_analysis",
            review_analysis_iteration=3,
            max_review_analysis_iterations=3,
            review_issues_found=False,
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK)
        assert new_state.phase == PHASE_FIX
        assert new_state.previous_phase == "review_analysis"
        assert new_state.review_analysis_iteration == state.max_review_analysis_iterations
        assert new_state.review_issues_found is True

    def test_review_analysis_loopback_with_zero_cap_stays_zero(self) -> None:
        """A zero configured cap should still route to fix without incrementing."""
        state = PipelineState(
            phase="review_analysis",
            review_analysis_iteration=0,
            max_review_analysis_iterations=0,
            review_issues_found=False,
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK)
        assert new_state.phase == PHASE_FIX
        assert new_state.previous_phase == "review_analysis"
        assert new_state.review_analysis_iteration == 0
        assert new_state.review_issues_found is True

    def test_review_analysis_loopback_at_max_with_policy_routes_to_fix(self) -> None:
        """Policy routing must keep review analysis loopback on fix when the cap is reached."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            review_analysis_iteration=1,
            max_review_analysis_iterations=2,
            review_issues_found=False,
        )

        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)

        assert new_state.phase == PHASE_FIX
        assert new_state.previous_phase == "review_analysis"
        assert new_state.review_analysis_iteration == state.max_review_analysis_iterations
        assert new_state.review_issues_found is True

    def test_review_analysis_success_resets_review_analysis_iteration(self) -> None:
        """ANALYSIS_SUCCESS in review_analysis resets the iteration counter."""
        state = PipelineState(
            phase="review_analysis",
            review_analysis_iteration=2,
            max_review_analysis_iterations=3,
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS)
        assert new_state.review_analysis_iteration == 0

    def test_commit_success_resets_review_analysis_iteration(self) -> None:
        """COMMIT_SUCCESS in review_commit resets review_analysis_iteration."""
        state = PipelineState(
            phase="review_commit",
            review_analysis_iteration=2,
            max_review_analysis_iterations=3,
        )
        new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS)
        assert new_state.review_analysis_iteration == 0

    def test_dev_analysis_loopback_routing_error_preserves_iteration_bookkeeping(self) -> None:
        """Routing errors after capped dev loopback should keep the clamped counter."""
        policy = MagicMock()
        phase_def = MagicMock()
        phase_def.requires_commit = False
        phase_def.embeds_analysis = False
        policy.phases.get.return_value = phase_def

        state = PipelineState(
            phase="development_analysis",
            development_analysis_iteration=1,
            max_development_analysis_iterations=3,
        )
        with patch("ralph.pipeline.reducer.resolve_next_phase", side_effect=ValueError("bad")):
            new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)

        assert new_state.phase == PHASE_FAILED
        assert new_state.development_analysis_iteration == min(
            state.development_analysis_iteration + 1,
            state.max_development_analysis_iterations,
        )

    def test_review_analysis_loopback_routing_error_preserves_iteration_bookkeeping(self) -> None:
        """Routing errors after capped review loopback should keep the clamped counter."""
        policy = MagicMock()
        phase_def = MagicMock()
        phase_def.requires_commit = False
        phase_def.embeds_analysis = False
        policy.phases.get.return_value = phase_def

        state = PipelineState(
            phase="review_analysis",
            review_analysis_iteration=1,
            max_review_analysis_iterations=3,
            review_issues_found=False,
        )
        with patch("ralph.pipeline.reducer.resolve_next_phase", side_effect=ValueError("bad")):
            new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)

        assert new_state.phase == PHASE_FAILED
        assert new_state.review_analysis_iteration == min(
            state.review_analysis_iteration + 1,
            state.max_review_analysis_iterations,
        )
        assert new_state.review_issues_found is True


@pytest.mark.parametrize(
    "event,handler_patch_target",
    [
        ("ANALYSIS_SUCCESS", "ralph.pipeline.reducer.resolve_next_phase"),
        ("ANALYSIS_LOOPBACK", "ralph.pipeline.reducer.resolve_next_phase"),
        ("REVIEW_ISSUES_FOUND", "ralph.pipeline.reducer.resolve_next_phase"),
        ("FIX_SUCCESS", "ralph.pipeline.reducer.resolve_next_phase"),
        ("COMMIT_SUCCESS", "ralph.pipeline.reducer.resolve_post_commit_phase"),
        ("PHASE_ADVANCE", "ralph.pipeline.reducer.resolve_next_phase"),
    ],
)
def test_routing_error_propagates_as_failure_not_silent(
    event: str, handler_patch_target: str
) -> None:
    """All reducer handlers must propagate ValueError from routing as pipeline failure."""
    policy = MagicMock()
    phase_def = MagicMock()
    phase_def.requires_commit = False
    phase_def.embeds_analysis = False
    policy.phases.get.return_value = phase_def

    with patch(handler_patch_target, side_effect=ValueError("unknown phase")):
        state = PipelineState(phase="review")
        new_state, effects = _reduce(state, getattr(PipelineEvent, event), policy)

    assert new_state.phase == PHASE_FAILED
    assert new_state.recovery_epoch == 1
    assert "unknown phase" in (new_state.last_error or "")
    assert effects == []


def test_agent_success_in_requires_commit_phase_marks_agent_invoked_without_advancing() -> None:
    """When AGENT_SUCCESS fires in a requires_commit phase, reducer must set
    commit.agent_invoked=True and keep the same phase — NOT advance to next phase."""
    policy = MagicMock()
    phase_def = MagicMock()
    phase_def.requires_commit = True
    phase_def.embeds_analysis = False
    policy.phases.get.return_value = phase_def

    state = PipelineState(phase="development_commit", commit=CommitState(agent_invoked=False))
    new_state, effects = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)

    assert new_state.phase == "development_commit"
    assert new_state.commit.agent_invoked is True
    assert effects == []


def test_agent_success_in_normal_phase_still_advances() -> None:
    """Sanity check: AGENT_SUCCESS in a normal (non-commit) phase still advances."""
    policy = MagicMock()
    phase_def = MagicMock()
    phase_def.requires_commit = False
    phase_def.embeds_analysis = False
    policy.phases.get.return_value = phase_def

    with patch("ralph.pipeline.reducer.resolve_next_phase", return_value="review"):
        state = PipelineState(phase="development")
        new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
    assert new_state.phase == "review"


# ---------------------------------------------------------------------------
# Fan-out parallelization lifecycle events
# ---------------------------------------------------------------------------


def _make_work_units(*ids: str) -> tuple[WorkUnit, ...]:
    return tuple(WorkUnit(unit_id=uid, description="task") for uid in ids)


def test_fan_out_started_initializes_worker_states() -> None:
    """FAN_OUT_STARTED should populate worker_states as PENDING for each work unit."""
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        work_units=_make_work_units("u1", "u2"),
    )
    new_state, effects = _reduce(state, PipelineEvent.FAN_OUT_STARTED)

    assert effects == []
    assert set(new_state.worker_states.keys()) == {"u1", "u2"}
    assert new_state.worker_states["u1"].status == WorkerStatus.PENDING
    assert new_state.worker_states["u2"].status == WorkerStatus.PENDING
    assert new_state.phase == PHASE_DEVELOPMENT


def test_fan_out_started_no_op_when_no_work_units() -> None:
    """FAN_OUT_STARTED should be a no-op when work_units is empty."""
    state = PipelineState(phase=PHASE_DEVELOPMENT)
    new_state, effects = _reduce(state, PipelineEvent.FAN_OUT_STARTED)

    assert new_state == state
    assert effects == []


def test_fan_out_started_no_op_when_worker_states_already_populated() -> None:
    """FAN_OUT_STARTED should be a no-op when worker_states is already populated."""
    pre_existing = WorkerState(unit_id="u1", status=WorkerStatus.RUNNING)
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        work_units=_make_work_units("u1"),
        worker_states={"u1": pre_existing},
    )
    new_state, effects = _reduce(state, PipelineEvent.FAN_OUT_STARTED)

    assert new_state == state
    assert effects == []


def test_worker_started_transitions_pending_to_running() -> None:
    """WORKER_STARTED should transition the named worker from PENDING to RUNNING."""
    pending = WorkerState(unit_id="u1", status=WorkerStatus.PENDING)
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        work_units=_make_work_units("u1"),
        worker_states={"u1": pending},
    )
    new_state, effects = _reduce(state, WorkerStartedEvent(unit_id="u1"))

    assert effects == []
    assert new_state.worker_states["u1"].status == WorkerStatus.RUNNING
    assert new_state.worker_states["u1"].started_at is not None


def test_worker_started_unknown_unit_id_is_no_op() -> None:
    """WORKER_STARTED for an unknown unit_id should leave state unchanged."""
    state = PipelineState(phase=PHASE_DEVELOPMENT)
    new_state, effects = _reduce(state, WorkerStartedEvent(unit_id="ghost"))

    assert new_state == state
    assert effects == []


def test_worker_completed_transitions_running_to_succeeded() -> None:
    """WORKER_COMPLETED should move the worker to SUCCEEDED and store commit_sha."""
    running = WorkerState(unit_id="u1", status=WorkerStatus.RUNNING)
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        work_units=_make_work_units("u1"),
        worker_states={"u1": running},
    )
    new_state, effects = _reduce(
        state,
        WorkerCompletedEvent(unit_id="u1", exit_code=0, commit_sha="abc123"),
    )

    assert effects == []
    ws = new_state.worker_states["u1"]
    assert ws.status == WorkerStatus.SUCCEEDED
    assert ws.exit_code == 0
    assert ws.commit_sha == "abc123"
    assert ws.finished_at is not None


def test_worker_completed_unknown_unit_id_is_no_op() -> None:
    """WORKER_COMPLETED for an unknown unit_id should leave state unchanged."""
    state = PipelineState(phase=PHASE_DEVELOPMENT)
    new_state, effects = _reduce(
        state,
        WorkerCompletedEvent(unit_id="ghost", exit_code=0, commit_sha="sha"),
    )

    assert new_state == state
    assert effects == []


def test_worker_failed_transitions_running_to_failed() -> None:
    """WORKER_FAILED should move the worker to FAILED and store error_message."""
    running = WorkerState(unit_id="u1", status=WorkerStatus.RUNNING)
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        work_units=_make_work_units("u1"),
        worker_states={"u1": running},
    )
    new_state, effects = _reduce(
        state,
        WorkerFailedEvent(unit_id="u1", exit_code=1, error="boom"),
    )

    assert effects == []
    ws = new_state.worker_states["u1"]
    assert ws.status == WorkerStatus.FAILED
    assert ws.exit_code == 1
    assert ws.error_message == "boom"
    assert ws.finished_at is not None


def test_worker_failed_unknown_unit_id_is_no_op() -> None:
    """WORKER_FAILED for an unknown unit_id should leave state unchanged."""
    state = PipelineState(phase=PHASE_DEVELOPMENT)
    new_state, effects = _reduce(
        state,
        WorkerFailedEvent(unit_id="ghost", exit_code=1, error="err"),
    )

    assert new_state == state
    assert effects == []


def test_all_workers_complete_advances_to_development_analysis() -> None:
    """ALL_WORKERS_COMPLETE should advance phase to DEVELOPMENT_ANALYSIS when all succeeded."""
    states = {
        "u1": WorkerState(unit_id="u1", status=WorkerStatus.SUCCEEDED),
        "u2": WorkerState(unit_id="u2", status=WorkerStatus.SUCCEEDED),
    }
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        work_units=_make_work_units("u1", "u2"),
        worker_states=states,
    )
    new_state, effects = _reduce(state, PipelineEvent.ALL_WORKERS_COMPLETE)

    assert effects == []
    assert new_state.phase == PHASE_DEVELOPMENT_ANALYSIS


def test_all_workers_complete_no_op_if_any_not_succeeded() -> None:
    """ALL_WORKERS_COMPLETE should leave state unchanged if any worker is not SUCCEEDED."""
    states = {
        "u1": WorkerState(unit_id="u1", status=WorkerStatus.SUCCEEDED),
        "u2": WorkerState(unit_id="u2", status=WorkerStatus.RUNNING),
    }
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        work_units=_make_work_units("u1", "u2"),
        worker_states=states,
    )
    new_state, effects = _reduce(state, PipelineEvent.ALL_WORKERS_COMPLETE)

    assert new_state == state
    assert effects == []


def test_all_workers_complete_routes_to_phase_failed_when_worker_failed() -> None:
    """ALL_WORKERS_COMPLETE must route to PHASE_FAILED when any worker has FAILED status."""
    states = {
        "u1": WorkerState(unit_id="u1", status=WorkerStatus.SUCCEEDED),
        "u2": WorkerState(unit_id="u2", status=WorkerStatus.FAILED),
    }
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        work_units=_make_work_units("u1", "u2"),
        worker_states=states,
    )
    new_state, effects = _reduce(state, PipelineEvent.ALL_WORKERS_COMPLETE)

    assert new_state.phase == PHASE_FAILED
    assert effects == []
    assert "u2" in (new_state.last_error or "")


def test_all_workers_complete_routes_to_phase_failed_when_worker_cancelled() -> None:
    """ALL_WORKERS_COMPLETE must route to PHASE_FAILED when any worker has CANCELLED status."""
    states = {
        "u1": WorkerState(unit_id="u1", status=WorkerStatus.CANCELLED),
        "u2": WorkerState(unit_id="u2", status=WorkerStatus.SUCCEEDED),
    }
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        work_units=_make_work_units("u1", "u2"),
        worker_states=states,
    )
    new_state, effects = _reduce(state, PipelineEvent.ALL_WORKERS_COMPLETE)

    assert new_state.phase == PHASE_FAILED
    assert effects == []
    assert "u1" in (new_state.last_error or "")


def test_workers_resumed_requeues_running_workers_as_pending() -> None:
    resumed_event = getattr(PipelineEvent, "WORKERS_RESUMED", None)
    assert resumed_event is not None

    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        work_units=_make_work_units("u1", "u2"),
        worker_states={
            "u1": WorkerState(unit_id="u1", status=WorkerStatus.RUNNING),
            "u2": WorkerState(unit_id="u2", status=WorkerStatus.SUCCEEDED),
        },
    )

    new_state, effects = _reduce(state, resumed_event)

    assert effects == []
    assert new_state.worker_states["u1"].status == WorkerStatus.PENDING
    assert new_state.worker_states["u2"].status == WorkerStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# COMMIT_SKIPPED — advances routing without incrementing iteration counters
# ---------------------------------------------------------------------------


def test_commit_skipped_advances_without_iteration_increment() -> None:
    """COMMIT_SKIPPED in development_commit must advance phase but NOT bump iteration."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(
        phase="development_commit",
        iteration=0,
        total_iterations=2,
        development_budget_remaining=1,
        review_budget_remaining=1,
    )

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)

    assert new_state.phase == "planning"
    assert new_state.previous_phase == "development_commit"
    assert new_state.iteration == 0


def test_commit_skipped_in_review_commit_advances_without_reviewer_pass_increment() -> None:
    """COMMIT_SKIPPED in review_commit must advance phase but NOT bump reviewer_pass."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(
        phase="review_commit",
        reviewer_pass=0,
        total_reviewer_passes=2,
        review_budget_remaining=1,
    )

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)

    assert new_state.phase == PHASE_REVIEW
    assert new_state.previous_phase == "review_commit"
    assert new_state.reviewer_pass == 0


def test_commit_skipped_routes_to_complete_when_budget_exhausted() -> None:
    """COMMIT_SKIPPED in review_commit routes to complete without bumping counters."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(
        phase="review_commit",
        reviewer_pass=0,
        review_budget_remaining=0,
    )

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)

    assert new_state.phase == PHASE_COMPLETE
    assert new_state.reviewer_pass == 0


def test_commit_skipped_without_policy_advances_to_complete() -> None:
    """Legacy path (no policy): COMMIT_SKIPPED advances directly to complete."""
    state = PipelineState(phase="review_commit", reviewer_pass=1)
    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SKIPPED)
    assert new_state.phase == PHASE_COMPLETE
    assert new_state.reviewer_pass == 1


# ---------------------------------------------------------------------------
# REVIEW_CLEAN — skips review_analysis and routes directly to review_commit
# ---------------------------------------------------------------------------


def test_review_clean_with_policy_routes_to_review_commit_not_analysis() -> None:
    """REVIEW_CLEAN with policy must route directly to review_commit (bypassing review_analysis)."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase=PHASE_REVIEW, review_budget_remaining=1)
    new_state, _ = _reduce(state, PipelineEvent.REVIEW_CLEAN, policy)

    assert new_state.phase == "review_commit"
    assert new_state.previous_phase == PHASE_REVIEW
    assert new_state.review_issues_found is False


def test_review_clean_without_policy_still_routes_to_review_commit() -> None:
    """Legacy path (no policy): REVIEW_CLEAN still routes to review_commit."""
    state = PipelineState(phase=PHASE_REVIEW, review_budget_remaining=1)
    new_state, _ = _reduce(state, PipelineEvent.REVIEW_CLEAN)

    assert new_state.phase == "review_commit"
    assert new_state.previous_phase == PHASE_REVIEW
    assert new_state.review_issues_found is False


# ---------------------------------------------------------------------------
# FULL NO-OP PIPELINE FLOW
# ---------------------------------------------------------------------------


def test_phase_handler_crash_exhausts_chain_before_failing() -> None:
    """PhaseFailureEvent(recoverable=True) must exhaust retries AND fallbacks before failing.

    This is the single most important regression guard for the bug where the pipeline
    exited on the first exception instead of going through the retry/fallback chain.
    """
    # State with a 2-agent dev_chain
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=["claude", "codex"], current_index=0, retries=0),
    )

    # PhaseFailureEvent that simulates a handler crash
    crash_event = PhaseFailureEvent(
        phase="development",
        reason="Phase handler crashed: RuntimeError: boom",
        recoverable=True,
    )

    # Agent 0: 3 retries (retries 0->1->2->3)
    for expected_retries in range(1, 4):
        state, effects = _reduce(state, crash_event)
        assert state.phase == PHASE_DEVELOPMENT
        assert state.dev_chain.current_index == 0
        assert state.dev_chain.retries == expected_retries
        assert effects == []

    # 4th crash on agent 0: fallback to agent 1 (retries reset to 0)
    state, effects = _reduce(state, crash_event)
    assert state.phase == PHASE_DEVELOPMENT
    assert state.dev_chain.current_index == 1
    assert state.dev_chain.retries == 0
    assert effects == []

    # Agent 1: 3 more retries (retries 0->1->2->3)
    for expected_retries in range(1, 4):
        state, effects = _reduce(state, crash_event)
        assert state.phase == PHASE_DEVELOPMENT
        assert state.dev_chain.current_index == 1
        assert state.dev_chain.retries == expected_retries
        assert effects == []

    # Final crash on agent 1 (chain exhausted): PHASE_FAILED recovery state
    state, effects = _reduce(state, crash_event)
    assert state.phase == PHASE_FAILED
    assert state.last_error is not None
    assert "Phase handler crashed: RuntimeError: boom" in state.last_error
    assert state.recovery_epoch == 1
    assert effects == []


def test_full_noop_pipeline_flow_reaches_complete_without_billing_counters() -> None:
    """End-to-end no-op pipeline.

    plan noop → dev skip → dev_analysis skip → dev_commit skip →
    review skip → review_commit skip → complete.

    All billing counters (iteration, reviewer_pass) must remain 0 throughout.
    """
    policy = _policy_with_post_commit_routes()

    # Step 1: planning → AGENT_SUCCESS (noop plan) → development
    # Budget starts at 2: first iteration consumes 1, second still has 1 left
    state = PipelineState(
        phase="planning",
        iteration=0,
        reviewer_pass=0,
        development_budget_remaining=2,
        review_budget_remaining=1,
    )
    new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
    assert new_state.phase == PHASE_DEVELOPMENT
    assert new_state.iteration == 0
    assert new_state.reviewer_pass == 0

    # Step 2: development → AGENT_SUCCESS → development_analysis
    state = new_state
    new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
    assert new_state.phase == "development_analysis"
    assert new_state.iteration == 0

    # Step 3: development_analysis → ANALYSIS_SUCCESS (noop short-circuit) → development_commit
    state = new_state
    new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)
    assert new_state.phase == "development_commit"
    assert new_state.iteration == 0

    # Step 4: development_commit → COMMIT_SKIPPED → planning (budget remaining)
    state = new_state
    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)
    assert new_state.phase == "planning"
    assert new_state.iteration == 0

    # Step 5: planning → AGENT_SUCCESS (noop again) → development
    state = new_state
    new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
    assert new_state.phase == PHASE_DEVELOPMENT

    # Step 6: development → AGENT_SUCCESS → development_analysis
    state = new_state
    new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
    assert new_state.phase == "development_analysis"

    # Step 7: development_analysis → ANALYSIS_SUCCESS → development_commit
    state = new_state
    new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)
    assert new_state.phase == "development_commit"

    # Step 8: development_commit → COMMIT_SKIPPED → review (budget exhausted)
    state = new_state.copy_with(development_budget_remaining=0)
    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)
    assert new_state.phase == PHASE_REVIEW
    assert new_state.iteration == 0

    # Step 9: review → REVIEW_CLEAN → review_commit (NOT review_analysis)
    state = new_state
    new_state, _ = _reduce(state, PipelineEvent.REVIEW_CLEAN, policy)
    assert new_state.phase == "review_commit"
    assert new_state.reviewer_pass == 0

    # Step 10: review_commit → COMMIT_SKIPPED → complete (budget exhausted)
    state = new_state.copy_with(review_budget_remaining=0)
    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)
    assert new_state.phase == PHASE_COMPLETE
    assert new_state.reviewer_pass == 0


def test_agent_success_with_no_policy_routes_through_failed_recovery() -> None:
    state = PipelineState(phase=PHASE_DEVELOPMENT, recovery_epoch=4, last_error=None)
    new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS)
    assert new_state.phase == PHASE_FAILED
    assert new_state.previous_phase == PHASE_DEVELOPMENT
    assert new_state.recovery_epoch == state.recovery_epoch + 1
    assert new_state.last_error == "No policy loaded for agent success routing"


def test_all_workers_complete_mixed_statuses_routes_to_phase_failed() -> None:
    """ALL_WORKERS_COMPLETE with mixed statuses: last_error must name
    all failed/cancelled units sorted."""
    states = {
        "u1": WorkerState(unit_id="u1", status=WorkerStatus.SUCCEEDED),
        "u2": WorkerState(unit_id="u2", status=WorkerStatus.FAILED),
        "u3": WorkerState(unit_id="u3", status=WorkerStatus.CANCELLED),
    }
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        work_units=_make_work_units("u1", "u2", "u3"),
        worker_states=states,
    )
    new_state, effects = _reduce(state, PipelineEvent.ALL_WORKERS_COMPLETE)

    assert new_state.phase == PHASE_FAILED
    assert effects == []
    error = new_state.last_error or ""
    assert "u2" in error
    assert "u3" in error
    assert error.index("u2") < error.index("u3"), (
        "Failed unit_ids must appear alphabetically: u2 before u3"
    )
