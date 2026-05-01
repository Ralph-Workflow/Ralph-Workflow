"""Unit tests for the pure pipeline reducer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, patch

import pytest

from ralph.pipeline.events import (
    PhaseFailureEvent,
    PipelineEvent,
    WorkerCompletedEvent,
    WorkerFailedEvent,
    WorkerStartedEvent,
)
from ralph.pipeline.progress import review_issues_found as _review_issues_found
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, CommitState, PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus
from ralph.policy.models import (
    BudgetCounterConfig,
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseTransition,
    PipelinePolicy,
    PostCommitRoute,
    PostCommitRouteWhen,
    RecoveryPolicy,
)

if TYPE_CHECKING:
    from ralph.config.enums import PipelinePhase
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
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="development",
                ),
            ),
            "failed_terminal": PhaseDefinition(
                drain="development",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )


def _policy_with_transition(target_phase: PipelinePhase) -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "budget_transition": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success=target_phase,
                    on_loopback="development",
                ),
            ),
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="development",
                ),
            ),
            "review": PhaseDefinition(
                drain="review",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="review",
                ),
            ),
        },
        entry_phase="budget_transition",
        terminal_phase="complete",
    )


def _policy_with_post_commit_routes() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                role="execution",
                transitions=PhaseTransition(on_success="development"),
            ),
            "development": PhaseDefinition(
                drain="development",
                role="execution",
                transitions=PhaseTransition(on_success="development_analysis"),
            ),
            "development_analysis": PhaseDefinition(
                drain="development_analysis",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="development_commit",
                    on_loopback="development",
                ),
                loop_policy=PhaseLoopPolicy(
                    max_iterations=3,
                    iteration_state_field="development_analysis_iteration",
                ),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                transitions=PhaseTransition(
                    on_success="review",
                ),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="iteration",
                    loop_resets=["development_analysis_iteration"],
                ),
            ),
            "review": PhaseDefinition(
                drain="review",
                role="review",
                clean_outcome="clean",
                issues_outcome="has_issues",
                transitions=PhaseTransition(on_success="review_analysis", on_loopback="fix"),
                bypass_routes={"clean": "review_commit"},
            ),
            "review_analysis": PhaseDefinition(
                drain="review_analysis",
                role="analysis",
                transitions=PhaseTransition(on_success="review_commit", on_loopback="fix"),
                loop_policy=PhaseLoopPolicy(
                    max_iterations=2,
                    iteration_state_field="review_analysis_iteration",
                    loopback_review_outcome="has_issues",
                ),
            ),
            "fix": PhaseDefinition(
                drain="fix",
                role="execution",
                transitions=PhaseTransition(
                    on_success="review_analysis",
                    on_loopback="review",
                ),
            ),
            "review_commit": PhaseDefinition(
                drain="review_commit",
                role="commit",
                transitions=PhaseTransition(
                    on_success="complete",
                ),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="reviewer_pass",
                    loop_resets=["review_analysis_iteration"],
                ),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
        budget_counters={
            "iteration": BudgetCounterConfig(),
            "reviewer_pass": BudgetCounterConfig(),
        },
        post_commit_routes=[
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="development_commit", budget_state="remaining"),
                target="planning",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="development_commit", budget_state="exhausted"),
                target="review",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="development_commit", budget_state="no_review"),
                target="complete",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="review_commit", budget_state="remaining"),
                target="review",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="review_commit", budget_state="exhausted"),
                target="complete",
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
            phase="development",
            phase_chains={"development": AgentChainState(agents=["claude"], current_index=0, retries=0)},  # noqa: E501
        )
        event = PhaseFailureEvent(phase="development", reason="missing artifact", recoverable=True)
        new_state, effects = _reduce(state, event)
        assert new_state.chain_for_phase("development").retries == 1
        assert new_state.phase == "development"
        assert effects == []

    def test_phase_failure_recoverable_after_3_retries_falls_back_to_next_agent(
        self,
    ) -> None:
        """After 3 retries, recoverable PhaseFailureEvent advances to next agent."""
        state = PipelineState(
            phase="development",
            phase_chains={"development": AgentChainState(agents=["claude", "opencode"], current_index=0, retries=3)},  # noqa: E501
        )
        event = PhaseFailureEvent(phase="development", reason="missing artifact", recoverable=True)
        new_state, effects = _reduce(state, event)
        assert new_state.chain_for_phase("development").current_index == 1
        assert new_state.chain_for_phase("development").retries == 0
        assert effects == []

    def test_phase_failure_recoverable_with_single_agent_after_3_retries_enters_recovery(
        self,
    ) -> None:
        """Single-agent chain exhaustion should enter recovery without exit effects."""
        state = PipelineState(
            phase="development",
            phase_chains={"development": AgentChainState(agents=["claude"], current_index=0, retries=3)},  # noqa: E501
        )
        event = PhaseFailureEvent(phase="development", reason="missing artifact", recoverable=True)
        new_state, effects = _reduce(state, event, _basic_pipeline_policy())
        assert new_state.phase == "failed_terminal"
        assert new_state.last_error is not None
        assert "development" in new_state.last_error
        assert "missing artifact" in new_state.last_error
        assert new_state.recovery_epoch == 1
        assert effects == []

    def test_phase_failure_not_recoverable_enters_recovery_without_exit_effect(
        self,
    ) -> None:
        """PhaseFailureEvent(recoverable=False) should still avoid process exit."""
        state = PipelineState(phase="development")
        event = PhaseFailureEvent(
            phase="development_analysis",
            reason="Analysis decision: FAILURE",
            recoverable=False,
        )
        new_state, effects = _reduce(state, event, _basic_pipeline_policy())
        assert new_state.phase == "failed_terminal"
        assert new_state.last_error == "development_analysis: Analysis decision: FAILURE"
        assert new_state.recovery_epoch == 1
        assert effects == []

    def test_phase_failure_recoverable_preserves_reason_in_last_error(self) -> None:
        """When chain exhausts, the original PhaseFailureEvent reason is preserved."""
        state = PipelineState(
            phase="development",
            phase_chains={"development": AgentChainState(agents=["claude"], current_index=0, retries=3)},  # noqa: E501
        )
        event = PhaseFailureEvent(
            phase="development",
            reason="Invalid development evidence: missing planning artifact",
            recoverable=True,
        )
        new_state, _effects = _reduce(state, event, _basic_pipeline_policy())
        assert new_state.phase == "failed_terminal"
        assert new_state.last_error is not None
        assert "missing planning artifact" in new_state.last_error
        assert "development" in new_state.last_error
        assert new_state.recovery_epoch == 1

    def test_phase_failure_never_produces_unknown_failure_string(self) -> None:
        """Terminal failure from PhaseFailureEvent must never show 'Unknown failure'."""
        state = PipelineState(
            phase="review",
            phase_chains={"review": AgentChainState(agents=["reviewer"], current_index=0, retries=3)},  # noqa: E501
        )
        event = PhaseFailureEvent(
            phase="review",
            reason="Missing/invalid issues artifact",
            recoverable=True,
        )
        new_state, effects = _reduce(state, event, _basic_pipeline_policy())
        assert new_state.phase == "failed_terminal"
        assert new_state.last_error is not None
        assert new_state.last_error != "Unknown failure"
        assert "Unknown failure" not in new_state.last_error
        assert new_state.recovery_epoch == 1
        assert effects == []

    def test_phase_failure_not_recoverable_routes_via_workflow_fallback_when_declared(
        self,
    ) -> None:
        """Non-recoverable PhaseFailureEvent routes to workflow_fallback.target when declared.

        Policy-declared workflow_fallback takes precedence over recovery.failed_route
        for non-recoverable failures, matching the same precedence used in
        _handle_agent_failure for chain exhaustion.
        """
        from ralph.policy.models import PhaseWorkflowFallback  # noqa: PLC0415

        policy = PipelinePolicy(
            phases={
                "development": PhaseDefinition(
                    drain="development",
                    workflow_fallback=PhaseWorkflowFallback(target="fallback_phase"),
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "fallback_phase": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "failed_terminal": PhaseDefinition(
                    drain="development",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed_terminal"),
                ),
            },
            entry_phase="development",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="failed_terminal"),
        )
        state = PipelineState(phase="development")
        event = PhaseFailureEvent(
            phase="development",
            reason="non-recoverable agent error",
            recoverable=False,
        )
        new_state, effects = _reduce(state, event, policy)
        assert new_state.phase == "fallback_phase", (
            f"Expected workflow_fallback target 'fallback_phase' but got '{new_state.phase}'"
        )
        assert new_state.last_error == "development: non-recoverable agent error"
        assert effects == []

    def test_phase_failure_not_recoverable_enters_terminal_when_no_workflow_fallback(
        self,
    ) -> None:
        """Non-recoverable PhaseFailureEvent without workflow_fallback routes to failed_route."""
        state = PipelineState(phase="development")
        event = PhaseFailureEvent(
            phase="development",
            reason="non-recoverable agent error",
            recoverable=False,
        )
        new_state, effects = _reduce(state, event, _basic_pipeline_policy())
        assert new_state.phase == "failed_terminal"
        assert new_state.recovery_epoch == 1
        assert effects == []


# =============================================================================
# Existing reducer tests (unchanged)
# =============================================================================


def test_policy_agent_success_in_development_routes_to_analysis() -> None:
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase="development")
    new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
    assert new_state.phase == "development_analysis"
    assert new_state.previous_phase == "development"


def test_policy_agent_success_in_planning_routes_to_development_without_consuming_budget() -> None:
    policy = _policy_with_post_commit_routes()
    starting_budget = 2
    state = PipelineState(phase="planning", budget_remaining={"iteration": starting_budget})
    new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
    assert new_state.phase == "development"
    assert new_state.get_budget_remaining("iteration") == starting_budget


def test_policy_agent_success_in_analysis_role_phase_routes_to_on_success() -> None:
    policy = PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="development_commit",
                    on_loopback="development",
                ),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )
    state = PipelineState(phase="development")
    new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
    assert new_state.phase == "development_commit"
    assert new_state.previous_phase == "development"


def test_review_clean_advances_to_commit() -> None:
    """Test that REVIEW_CLEAN advances to commit phase."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase="review")
    new_state, _ = _reduce(state, PipelineEvent.REVIEW_CLEAN, policy)
    assert new_state.phase == "review_commit"


def test_review_issues_found_advances_to_fix_without_completing_pass() -> None:
    """REVIEW_ISSUES_FOUND should route to fix without incrementing reviewer_pass."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(
        phase="review",
        reviewer_pass=0,
        budget_caps={"reviewer_pass": 2},
    )
    new_state, _ = _reduce(state, PipelineEvent.REVIEW_ISSUES_FOUND, policy)
    assert new_state.phase == "fix"
    assert new_state.reviewer_pass == 0
    assert _review_issues_found(new_state, policy) is True


def test_fix_success_returns_to_review_analysis() -> None:
    """Test that FIX_SUCCESS returns to review_analysis phase for verification."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(
        phase="fix",
        reviewer_pass=0,
        budget_caps={"reviewer_pass": 2},
    )
    new_state, _ = _reduce(state, PipelineEvent.FIX_SUCCESS, policy)
    assert new_state.phase == "review_analysis"


def test_commit_success_advances_to_complete() -> None:
    """Test that COMMIT_SUCCESS advances to complete phase."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase="review_commit", budget_remaining={"reviewer_pass": 0})
    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)
    assert new_state.phase == "complete"


def test_policy_agent_success_unknown_phase_routes_to_failed() -> None:
    """Policy-driven agent success should fail when phase is missing from policy."""
    state = PipelineState(phase="missing")
    policy = _basic_pipeline_policy()

    new_state, effects = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)

    assert new_state.phase == "failed_terminal"
    assert new_state.previous_phase == "missing"
    assert new_state.last_error == "Unknown phase: missing"
    assert new_state.recovery_epoch == 1
    assert effects == []


def test_phase_advance_event_fails_on_unknown_policy_phase() -> None:
    """PHASE_ADVANCE should fail with a routing error when the phase is missing from the policy."""
    state = PipelineState(phase="missing")
    policy = _basic_pipeline_policy()

    new_state, effects = _reduce(state, PipelineEvent.PHASE_ADVANCE, policy)

    assert new_state.phase == "failed_terminal"
    assert new_state.previous_phase == "missing"
    assert new_state.recovery_epoch == 1
    assert "missing" in (new_state.last_error or "")
    assert effects == []


def test_fix_failure_policy_terminal_transition_emits_exit_failure() -> None:
    """FIX_FAILURE should fail the pipeline when the policy points to a terminal phase."""
    policy = PipelinePolicy(
        phases={
            "fix": PhaseDefinition(
                drain="fix",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="fix",
                ),
            ),
            "failed_terminal": PhaseDefinition(
                drain="fix",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase="fix",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )
    state = PipelineState(phase="fix")

    new_state, effects = _reduce(state, PipelineEvent.FIX_FAILURE, policy)

    assert new_state.phase == "failed_terminal"
    assert new_state.previous_phase == "fix"
    assert new_state.recovery_epoch == 1
    assert effects == []


def test_phase_advance_preserves_development_budget_until_commit_outcome() -> None:
    """Advancing to development should not consume budget before commit outcome."""
    policy = _policy_with_transition("development")
    starting_budget = 2
    state = PipelineState(
        phase="budget_transition",
        budget_remaining={"iteration": starting_budget},
    )

    new_state, _ = _reduce(state, PipelineEvent.PHASE_ADVANCE, policy)

    assert new_state.phase == "development"
    assert new_state.get_budget_remaining("iteration") == starting_budget


def test_phase_advance_updates_current_drain_from_target_phase_policy() -> None:
    """Advancing phases should update the authoritative current drain from policy."""
    policy = _policy_with_transition("development")
    state = PipelineState(phase="budget_transition", current_drain="planning")

    new_state, _ = _reduce(state, PipelineEvent.PHASE_ADVANCE, policy)

    assert new_state.phase == "development"
    assert new_state.current_drain == "development"


def test_phase_advance_clamps_review_budget_at_zero() -> None:
    """Review budget should never go negative when advancing to review."""
    policy = _policy_with_transition("review")
    state = PipelineState(phase="budget_transition", budget_remaining={"reviewer_pass": 0})

    new_state, _ = _reduce(state, PipelineEvent.PHASE_ADVANCE, policy)

    assert new_state.phase == "review"
    assert new_state.get_budget_remaining("reviewer_pass") == 0


def test_commit_success_routes_development_commit_to_planning_when_budget_still_remains() -> None:
    """COMMIT_SUCCESS should route development_commit to planning when budget remains.

    The reducer now consumes budget before evaluating the post-commit route.
    """
    policy = _policy_with_post_commit_routes()
    state = PipelineState(
        phase="development_commit",
        budget_remaining={"iteration": 2, "reviewer_pass": 1},
    )

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert new_state.phase == "planning"
    assert new_state.previous_phase == "development_commit"
    assert new_state.current_drain == "planning"
    assert new_state.get_budget_remaining("iteration") == 1


def test_commit_success_increments_development_iteration_with_policy() -> None:
    """A completed development commit should advance the visible iteration counter."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(
        phase="development_commit",
        iteration=0,
        budget_caps={"iteration": 2},
        budget_remaining={"iteration": 1, "reviewer_pass": 1},
    )

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert new_state.phase == "review"
    assert new_state.iteration == 1
    assert new_state.get_budget_remaining("iteration") == 0


def test_commit_success_routes_development_commit_to_review_when_budget_exhausted() -> None:
    """COMMIT_SUCCESS should route development_commit to review when budget exhausted."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(
        phase="development_commit",
        budget_remaining={"iteration": 0, "reviewer_pass": 1},
    )

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert new_state.phase == "review"
    assert new_state.previous_phase == "development_commit"


def test_commit_success_routes_review_commit_to_review_when_budget_still_remains() -> None:
    """COMMIT_SUCCESS should route review_commit to review when budget remains after consumption."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase="review_commit", budget_remaining={"reviewer_pass": 2})

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert new_state.phase == "review"
    assert new_state.previous_phase == "review_commit"
    assert new_state.get_budget_remaining("reviewer_pass") == 1


def test_commit_success_increments_reviewer_pass_with_policy() -> None:
    """A completed review commit should advance the visible reviewer pass counter."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(
        phase="review_commit",
        reviewer_pass=0,
        budget_caps={"reviewer_pass": 2},
        budget_remaining={"reviewer_pass": 1},
    )

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert new_state.phase == "complete"
    assert new_state.reviewer_pass == 1
    assert new_state.get_budget_remaining("reviewer_pass") == 0


def test_commit_success_routes_review_commit_to_complete_when_budget_exhausted() -> None:
    """COMMIT_SUCCESS should route review_commit to complete when budget exhausted."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase="review_commit", budget_remaining={"reviewer_pass": 0})

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert new_state.phase == "complete"
    assert new_state.previous_phase == "review_commit"


def test_agent_failure_triggers_retry() -> None:
    """Test that AGENT_FAILURE increments retry count."""
    state = PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=["claude"], current_index=0, retries=0)},  # noqa: E501
    )
    new_state, _ = _reduce(state, PipelineEvent.AGENT_FAILURE)
    assert new_state.chain_for_phase("development").retries == 1


def test_agent_failure_falls_back_to_next_agent() -> None:
    """Test that AGENT_FAILURE falls back to next agent after max retries."""
    state = PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=["claude", "opencode"], current_index=0, retries=3)},  # noqa: E501
    )
    new_state, _ = _reduce(state, PipelineEvent.AGENT_FAILURE)
    assert new_state.chain_for_phase("development").current_index == 1
    assert new_state.chain_for_phase("development").retries == 0


def test_agent_failure_with_exhausted_chain_enters_recovery() -> None:
    """AGENT_FAILURE with exhausted chain should enter recovery without exit effects."""
    state = PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=["claude"], current_index=0, retries=3)},  # noqa: E501
    )
    new_state, effects = _reduce(state, PipelineEvent.AGENT_FAILURE, _basic_pipeline_policy())
    assert new_state.phase == "failed_terminal"
    assert new_state.recovery_epoch == 1
    assert effects == []


def test_planning_agent_failure_uses_planning_chain_instead_of_review_chain() -> None:
    state = PipelineState(
        phase="planning",
        phase_chains={
            "planning": AgentChainState(agents=["claude", "opencode"], current_index=0, retries=0),
            "review": AgentChainState(agents=["reviewer"], current_index=0, retries=0),
        },
    )

    new_state, _ = _reduce(state, PipelineEvent.AGENT_FAILURE)

    assert new_state.chain_for_phase("planning").retries == 1
    assert new_state.chain_for_phase("review").retries == 0


def test_checkpoint_saved_increments_count() -> None:
    """Test that CHECKPOINT_SAVED increments the checkpoint counter."""
    state = PipelineState(phase="planning", checkpoint_saved_count=0)
    new_state, _ = _reduce(state, PipelineEvent.CHECKPOINT_SAVED)
    assert new_state.checkpoint_saved_count == 1


def test_interrupted_sets_flag() -> None:
    """Test that INTERRUPTED sets the interrupted_by_user flag."""
    state = PipelineState(phase="planning", interrupted_by_user=False)
    new_state, _ = _reduce(state, PipelineEvent.INTERRUPTED)
    assert new_state.interrupted_by_user is True


def test_is_complete_returns_true_for_complete() -> None:
    """is_complete returns True when phase matches policy.terminal_phase."""
    policy = _basic_pipeline_policy()
    state = PipelineState(phase="complete")
    assert state.is_complete(policy) is True


def test_is_complete_returns_false_for_failed() -> None:
    """Failed phase is not the terminal phase and must not be treated as complete."""
    policy = _basic_pipeline_policy()
    state = PipelineState(phase="failed")
    assert state.is_complete(policy) is False


def test_is_complete_returns_false_for_development() -> None:
    """Non-terminal phase must not be treated as complete."""
    policy = _basic_pipeline_policy()
    state = PipelineState(phase="development")
    assert state.is_complete(policy) is False


class TestAnalysisDecisionDispatch:
    """Tests for AnalysisDecision-driven routing through the reducer.

    These tests verify that ANALYSIS_SUCCESS and ANALYSIS_LOOPBACK events
    (emitted by the phase handlers based on AnalysisDecision values)
    correctly route between development_analysis/review_analysis and their
    target phases.
    """

    def test_analysis_success_routes_development_analysis_to_commit(self) -> None:
        """Test that ANALYSIS_SUCCESS in development_analysis routes to development_commit."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="development_analysis",
            iteration=0,
            budget_caps={"iteration": 2},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)
        assert new_state.phase == "development_commit"
        assert new_state.previous_phase == "development_analysis"

    def test_analysis_loopback_routes_development_analysis_to_development(self) -> None:
        """Test that ANALYSIS_LOOPBACK in development_analysis routes back to development."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="development_analysis",
            iteration=0,
            budget_caps={"iteration": 2},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "development"
        assert new_state.previous_phase == "development_analysis"

    def test_analysis_loopback_does_not_decrement_budget(self) -> None:
        """Analysis loopback re-enters development WITHOUT consuming a budget slot."""
        initial_budget = 2
        state = PipelineState(
            phase="development_analysis",
            budget_remaining={"iteration": initial_budget},
        )
        policy = _policy_with_post_commit_routes()
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "development"
        assert new_state.get_budget_remaining("iteration") == initial_budget

    def test_analysis_success_routes_review_analysis_to_commit(self) -> None:
        """Test that ANALYSIS_SUCCESS in review_analysis routes to review_commit."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            reviewer_pass=0,
            budget_caps={"reviewer_pass": 2},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)
        assert new_state.phase == "review_commit"
        assert new_state.previous_phase == "review_analysis"

    def test_analysis_loopback_routes_review_analysis_to_fix(self) -> None:
        """Test that ANALYSIS_LOOPBACK in review_analysis routes to fix."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            reviewer_pass=0,
            budget_caps={"reviewer_pass": 2},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "fix"
        assert new_state.previous_phase == "review_analysis"

    def test_analysis_loopback_with_policy_marks_review_issue_without_completing_pass(self) -> None:
        """Policy routing must preserve review bookkeeping on loopback to fix."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            reviewer_pass=0,
            budget_caps={"reviewer_pass": 2},
        )

        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)

        assert new_state.phase == "fix"
        assert new_state.reviewer_pass == 0
        assert _review_issues_found(new_state, policy) is True

    def test_analysis_success_with_policy_clears_review_issue_flag(self) -> None:
        """Policy routing should clear stale review issue state on approval."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            reviewer_pass=1,
            budget_caps={"reviewer_pass": 2},
        )

        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)

        assert new_state.phase == "review_commit"
        assert _review_issues_found(new_state, policy) is False

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
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="development_analysis",
            loop_iterations={"development_analysis_iteration": 0},
            loop_caps={"development_analysis_iteration": 3},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.get_loop_iteration("development_analysis_iteration") == 1

    def test_dev_analysis_loopback_at_max_routes_to_development(self) -> None:
        """At max iterations, ANALYSIS_LOOPBACK still routes to development."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="development_analysis",
            loop_iterations={"development_analysis_iteration": 2},
            loop_caps={"development_analysis_iteration": 3},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "development"
        assert new_state.previous_phase == "development_analysis"
        assert (
            new_state.get_loop_iteration("development_analysis_iteration")
            == state.loop_caps.get("development_analysis_iteration", 3)
        )

    def test_dev_analysis_loopback_already_at_cap_stays_clamped(self) -> None:
        """Further loopbacks after the cap should not increment beyond the cap."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="development_analysis",
            loop_iterations={"development_analysis_iteration": 3},
            loop_caps={"development_analysis_iteration": 3},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "development"
        assert new_state.previous_phase == "development_analysis"
        assert (
            new_state.get_loop_iteration("development_analysis_iteration")
            == state.loop_caps.get("development_analysis_iteration", 3)
        )

    def test_dev_analysis_loopback_with_zero_cap_stays_zero(self) -> None:
        """A zero configured cap should still route to development without incrementing."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="development_analysis",
            loop_iterations={"development_analysis_iteration": 0},
            loop_caps={"development_analysis_iteration": 0},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "development"
        assert new_state.previous_phase == "development_analysis"
        assert new_state.get_loop_iteration("development_analysis_iteration") == 0

    def test_dev_analysis_success_resets_dev_analysis_iteration(self) -> None:
        """ANALYSIS_SUCCESS in development_analysis resets the iteration counter."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="development_analysis",
            loop_iterations={"development_analysis_iteration": 2},
            loop_caps={"development_analysis_iteration": 3},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)
        assert new_state.get_loop_iteration("development_analysis_iteration") == 0

    def test_commit_success_resets_dev_analysis_iteration(self) -> None:
        """COMMIT_SUCCESS in development_commit resets development_analysis_iteration."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="development_commit",
            loop_iterations={"development_analysis_iteration": 2},
            loop_caps={"development_analysis_iteration": 3},
            budget_remaining={"iteration": 0},
        )
        new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)
        assert new_state.get_loop_iteration("development_analysis_iteration") == 0

    def test_review_analysis_loopback_increments_review_analysis_iteration(self) -> None:
        """ANALYSIS_LOOPBACK in review_analysis increments the iteration counter."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            loop_iterations={"review_analysis_iteration": 0},
            loop_caps={"review_analysis_iteration": 3},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.get_loop_iteration("review_analysis_iteration") == 1

    def test_review_analysis_loopback_at_max_routes_to_fix(self) -> None:
        """At the review-analysis cap, ANALYSIS_LOOPBACK still routes to fix."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            loop_iterations={"review_analysis_iteration": 2},
            loop_caps={"review_analysis_iteration": 3},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "fix"
        assert new_state.previous_phase == "review_analysis"
        assert (
            new_state.get_loop_iteration("review_analysis_iteration")
            == state.loop_caps.get("review_analysis_iteration", 2)
        )

    def test_review_analysis_loopback_already_at_cap_stays_clamped(self) -> None:
        """Further review loopbacks after the cap should not increment beyond the cap."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            loop_iterations={"review_analysis_iteration": 3},
            loop_caps={"review_analysis_iteration": 3},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "fix"
        assert new_state.previous_phase == "review_analysis"
        assert (
            new_state.get_loop_iteration("review_analysis_iteration")
            == state.loop_caps.get("review_analysis_iteration", 2)
        )
        assert new_state.review_outcome is not None

    def test_review_analysis_loopback_with_zero_cap_stays_zero(self) -> None:
        """A zero configured cap should still route to fix without incrementing."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            loop_iterations={"review_analysis_iteration": 0},
            loop_caps={"review_analysis_iteration": 0},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "fix"
        assert new_state.previous_phase == "review_analysis"
        assert new_state.get_loop_iteration("review_analysis_iteration") == 0
        assert new_state.review_outcome is not None

    def test_review_analysis_loopback_at_max_with_policy_routes_to_fix(self) -> None:
        """Policy routing must keep review analysis loopback on fix when the cap is reached."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            loop_iterations={"review_analysis_iteration": 1},
            loop_caps={"review_analysis_iteration": 2},
        )

        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)

        assert new_state.phase == "fix"
        assert new_state.previous_phase == "review_analysis"
        assert (
            new_state.get_loop_iteration("review_analysis_iteration")
            == state.loop_caps.get("review_analysis_iteration", 2)
        )
        assert new_state.review_outcome is not None

    def test_review_analysis_success_resets_review_analysis_iteration(self) -> None:
        """ANALYSIS_SUCCESS in review_analysis resets the iteration counter."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            loop_iterations={"review_analysis_iteration": 2},
            loop_caps={"review_analysis_iteration": 3},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)
        assert new_state.get_loop_iteration("review_analysis_iteration") == 0

    def test_commit_success_resets_review_analysis_iteration(self) -> None:
        """COMMIT_SUCCESS in review_commit resets review_analysis_iteration."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_commit",
            loop_iterations={"review_analysis_iteration": 2},
            loop_caps={"review_analysis_iteration": 3},
            budget_remaining={"reviewer_pass": 0},
        )
        new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)
        assert new_state.get_loop_iteration("review_analysis_iteration") == 0

    def test_dev_analysis_loopback_routing_error_preserves_iteration_bookkeeping(self) -> None:
        """Routing errors after capped dev loopback should keep the clamped counter."""
        policy = MagicMock()
        policy.recovery.failed_route = "failed"
        phase_def = MagicMock()
        phase_def.workflow_fallback = None
        phase_def.loop_policy = PhaseLoopPolicy(
            max_iterations=3,
            iteration_state_field="development_analysis_iteration",
        )
        phase_def.decisions = {}
        policy.phases.get.return_value = phase_def

        state = PipelineState(
            phase="development_analysis",
            loop_iterations={"development_analysis_iteration": 1},
            loop_caps={"development_analysis_iteration": 3},
        )
        with patch("ralph.pipeline.reducer.resolve_next_phase", side_effect=ValueError("bad")):
            new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)

        assert new_state.phase == "failed"
        assert new_state.get_loop_iteration("development_analysis_iteration") == min(
            state.get_loop_iteration("development_analysis_iteration") + 1,
            state.loop_caps.get("development_analysis_iteration", 3),
        )

    def test_review_analysis_loopback_routing_error_preserves_iteration_bookkeeping(self) -> None:
        """Routing errors after capped review loopback should keep the clamped counter."""
        policy = MagicMock()
        policy.recovery.failed_route = "failed"
        phase_def = MagicMock()
        phase_def.workflow_fallback = None
        phase_def.loop_policy = PhaseLoopPolicy(
            max_iterations=3,
            iteration_state_field="review_analysis_iteration",
            loopback_review_outcome="has_issues",
        )
        phase_def.decisions = {}
        policy.phases.get.return_value = phase_def

        state = PipelineState(
            phase="review_analysis",
            loop_iterations={"review_analysis_iteration": 1},
            loop_caps={"review_analysis_iteration": 3},
        )
        with patch("ralph.pipeline.reducer.resolve_next_phase", side_effect=ValueError("bad")):
            new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)

        assert new_state.phase == "failed"
        assert new_state.get_loop_iteration("review_analysis_iteration") == min(
            state.get_loop_iteration("review_analysis_iteration") + 1,
            state.loop_caps.get("review_analysis_iteration", 2),
        )
        assert new_state.review_outcome is not None


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
    policy.recovery.failed_route = "failed"
    phase_def = MagicMock()
    phase_def.workflow_fallback = None
    phase_def.decisions = {}
    policy.phases.get.return_value = phase_def

    with patch(handler_patch_target, side_effect=ValueError("unknown phase")):
        state = PipelineState(phase="review")
        new_state, effects = _reduce(state, getattr(PipelineEvent, event), policy)

    assert new_state.phase == "failed"
    assert new_state.recovery_epoch == 1
    assert "unknown phase" in (new_state.last_error or "")
    assert effects == []


def test_agent_success_in_requires_commit_phase_marks_agent_invoked_without_advancing() -> None:
    """When AGENT_SUCCESS fires in a commit phase (role='commit'), reducer must set
    commit.agent_invoked=True and keep the same phase — NOT advance to next phase."""
    policy = MagicMock()
    phase_def = MagicMock()
    phase_def.role = "commit"
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
    phase_def.workflow_fallback = None
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
        phase="development",
        work_units=_make_work_units("u1", "u2"),
    )
    new_state, effects = _reduce(state, PipelineEvent.FAN_OUT_STARTED)

    assert effects == []
    assert set(new_state.worker_states.keys()) == {"u1", "u2"}
    assert new_state.worker_states["u1"].status == WorkerStatus.PENDING
    assert new_state.worker_states["u2"].status == WorkerStatus.PENDING
    assert new_state.phase == "development"


def test_fan_out_started_no_op_when_no_work_units() -> None:
    """FAN_OUT_STARTED should be a no-op when work_units is empty."""
    state = PipelineState(phase="development")
    new_state, effects = _reduce(state, PipelineEvent.FAN_OUT_STARTED)

    assert new_state == state
    assert effects == []


def test_fan_out_started_no_op_when_worker_states_already_populated() -> None:
    """FAN_OUT_STARTED should be a no-op when worker_states is already populated."""
    pre_existing = WorkerState(unit_id="u1", status=WorkerStatus.RUNNING)
    state = PipelineState(
        phase="development",
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
        phase="development",
        work_units=_make_work_units("u1"),
        worker_states={"u1": pending},
    )
    new_state, effects = _reduce(state, WorkerStartedEvent(unit_id="u1"))

    assert effects == []
    assert new_state.worker_states["u1"].status == WorkerStatus.RUNNING
    assert new_state.worker_states["u1"].started_at is not None


def test_worker_started_unknown_unit_id_is_no_op() -> None:
    """WORKER_STARTED for an unknown unit_id should leave state unchanged."""
    state = PipelineState(phase="development")
    new_state, effects = _reduce(state, WorkerStartedEvent(unit_id="ghost"))

    assert new_state == state
    assert effects == []


def test_worker_completed_transitions_running_to_succeeded() -> None:
    """WORKER_COMPLETED should move the worker to SUCCEEDED."""
    running = WorkerState(unit_id="u1", status=WorkerStatus.RUNNING)
    state = PipelineState(
        phase="development",
        work_units=_make_work_units("u1"),
        worker_states={"u1": running},
    )
    new_state, effects = _reduce(
        state,
        WorkerCompletedEvent(unit_id="u1", exit_code=0),
    )

    assert effects == []
    ws = new_state.worker_states["u1"]
    assert ws.status == WorkerStatus.SUCCEEDED
    assert ws.exit_code == 0
    assert ws.finished_at is not None


def test_worker_completed_unknown_unit_id_is_no_op() -> None:
    """WORKER_COMPLETED for an unknown unit_id should leave state unchanged."""
    state = PipelineState(phase="development")
    new_state, effects = _reduce(
        state,
        WorkerCompletedEvent(unit_id="ghost", exit_code=0),
    )

    assert new_state == state
    assert effects == []


def test_worker_failed_transitions_running_to_failed() -> None:
    """WORKER_FAILED should move the worker to FAILED and store error_message."""
    running = WorkerState(unit_id="u1", status=WorkerStatus.RUNNING)
    state = PipelineState(
        phase="development",
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
    state = PipelineState(phase="development")
    new_state, effects = _reduce(
        state,
        WorkerFailedEvent(unit_id="ghost", exit_code=1, error="err"),
    )

    assert new_state == state
    assert effects == []


def test_all_workers_complete_advances_to_development_analysis() -> None:
    """ALL_WORKERS_COMPLETE should advance phase to DEVELOPMENT_ANALYSIS when all succeeded."""
    policy = _policy_with_post_commit_routes()
    states = {
        "u1": WorkerState(unit_id="u1", status=WorkerStatus.SUCCEEDED),
        "u2": WorkerState(unit_id="u2", status=WorkerStatus.SUCCEEDED),
    }
    state = PipelineState(
        phase="development",
        work_units=_make_work_units("u1", "u2"),
        worker_states=states,
    )
    new_state, effects = _reduce(state, PipelineEvent.ALL_WORKERS_COMPLETE, policy)

    assert effects == []
    assert new_state.phase == "development_analysis"


def test_all_workers_complete_no_op_if_any_not_succeeded() -> None:
    """ALL_WORKERS_COMPLETE should leave state unchanged if any worker is not SUCCEEDED."""
    states = {
        "u1": WorkerState(unit_id="u1", status=WorkerStatus.SUCCEEDED),
        "u2": WorkerState(unit_id="u2", status=WorkerStatus.RUNNING),
    }
    state = PipelineState(
        phase="development",
        work_units=_make_work_units("u1", "u2"),
        worker_states=states,
    )
    new_state, effects = _reduce(state, PipelineEvent.ALL_WORKERS_COMPLETE)

    assert new_state == state
    assert effects == []


def test_all_workers_complete_routes_to_phase_failed_when_worker_failed() -> None:
    """ALL_WORKERS_COMPLETE must route to "failed" when any worker has FAILED status."""
    states = {
        "u1": WorkerState(unit_id="u1", status=WorkerStatus.SUCCEEDED),
        "u2": WorkerState(unit_id="u2", status=WorkerStatus.FAILED),
    }
    state = PipelineState(
        phase="development",
        work_units=_make_work_units("u1", "u2"),
        worker_states=states,
    )
    policy = _basic_pipeline_policy()
    new_state, effects = _reduce(state, PipelineEvent.ALL_WORKERS_COMPLETE, policy)

    assert new_state.phase == "failed_terminal"
    assert effects == []
    assert "u2" in (new_state.last_error or "")


def test_all_workers_complete_routes_to_phase_failed_when_worker_cancelled() -> None:
    """ALL_WORKERS_COMPLETE must route to "failed" when any worker has CANCELLED status."""
    states = {
        "u1": WorkerState(unit_id="u1", status=WorkerStatus.CANCELLED),
        "u2": WorkerState(unit_id="u2", status=WorkerStatus.SUCCEEDED),
    }
    state = PipelineState(
        phase="development",
        work_units=_make_work_units("u1", "u2"),
        worker_states=states,
    )
    policy = _basic_pipeline_policy()
    new_state, effects = _reduce(state, PipelineEvent.ALL_WORKERS_COMPLETE, policy)

    assert new_state.phase == "failed_terminal"
    assert effects == []
    assert "u1" in (new_state.last_error or "")


def test_workers_resumed_requeues_running_workers_as_pending() -> None:
    resumed_event = getattr(PipelineEvent, "WORKERS_RESUMED", None)
    assert resumed_event is not None

    state = PipelineState(
        phase="development",
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
        budget_caps={"iteration": 2},
        budget_remaining={"iteration": 1, "reviewer_pass": 1},
    )

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)

    assert new_state.phase == "review"
    assert new_state.previous_phase == "development_commit"
    assert new_state.iteration == 0
    assert new_state.get_budget_remaining("iteration") == 0


def test_commit_skipped_in_review_commit_advances_without_reviewer_pass_increment() -> None:
    """COMMIT_SKIPPED in review_commit must advance phase but NOT bump reviewer_pass."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(
        phase="review_commit",
        reviewer_pass=0,
        budget_caps={"reviewer_pass": 2},
        budget_remaining={"reviewer_pass": 1},
    )

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)

    assert new_state.phase == "complete"
    assert new_state.previous_phase == "review_commit"
    assert new_state.reviewer_pass == 0
    assert new_state.get_budget_remaining("reviewer_pass") == 0


def test_commit_skipped_routes_to_complete_when_budget_exhausted() -> None:
    """COMMIT_SKIPPED in review_commit routes to complete without bumping counters."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(
        phase="review_commit",
        reviewer_pass=0,
        budget_remaining={"reviewer_pass": 0},
    )

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)

    assert new_state.phase == "complete"
    assert new_state.reviewer_pass == 0


def test_commit_skipped_without_policy_advances_to_complete() -> None:
    """COMMIT_SKIPPED on review_commit with exhausted budget advances to complete."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(
        phase="review_commit",
        reviewer_pass=1,
        budget_remaining={"reviewer_pass": 0},
    )
    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)
    assert new_state.phase == "complete"
    assert new_state.reviewer_pass == 1


# ---------------------------------------------------------------------------
# REVIEW_CLEAN — skips review_analysis and routes directly to review_commit
# ---------------------------------------------------------------------------


def test_review_clean_with_policy_routes_to_review_commit_not_analysis() -> None:
    """REVIEW_CLEAN with policy must route directly to review_commit (bypassing review_analysis)."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase="review", budget_remaining={"reviewer_pass": 1})
    new_state, _ = _reduce(state, PipelineEvent.REVIEW_CLEAN, policy)

    assert new_state.phase == "review_commit"
    assert new_state.previous_phase == "review"
    assert new_state.review_outcome is None


def test_review_clean_via_bypass_routes_skips_analysis() -> None:
    """REVIEW_CLEAN routes directly to review_commit via bypass_routes, skipping review_analysis."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase="review", budget_remaining={"reviewer_pass": 1})
    new_state, _ = _reduce(state, PipelineEvent.REVIEW_CLEAN, policy)

    assert new_state.phase == "review_commit"
    assert new_state.previous_phase == "review"
    assert new_state.review_outcome is None



# ---------------------------------------------------------------------------
# Policy-driven analysis routing (no hardcoded decision-key lookup)
# ---------------------------------------------------------------------------


def test_analysis_success_routes_via_transitions_only() -> None:
    """ANALYSIS_SUCCESS must route via transitions.on_success, not via decisions dict."""
    policy = _policy_with_post_commit_routes()
    # development_analysis.transitions.on_success = "development_commit"
    state = PipelineState(phase="development_analysis")
    new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)
    assert new_state.phase == "development_commit"


def test_analysis_loopback_routes_via_transitions_only() -> None:
    """ANALYSIS_LOOPBACK must route via transitions.on_loopback, not via decisions dict."""
    policy = _policy_with_post_commit_routes()
    # development_analysis.transitions.on_loopback = "development"
    state = PipelineState(phase="development_analysis")
    new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
    assert new_state.phase == "development"


def test_review_clean_uses_policy_clean_outcome() -> None:
    """REVIEW_CLEAN reads bypass_routes key from phase_def.clean_outcome, not hardcoded string."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase="review")
    new_state, _ = _reduce(state, PipelineEvent.REVIEW_CLEAN, policy)
    # clean_outcome="clean" -> bypass_routes["clean"] = "review_commit"
    assert new_state.phase == "review_commit"
    assert new_state.review_outcome is None


def test_review_clean_without_bypass_routes_uses_on_success() -> None:
    """REVIEW_CLEAN with no clean_outcome or bypass_routes falls back to transitions.on_success."""
    policy = PipelinePolicy(
        phases={
            "review": PhaseDefinition(
                drain="review",
                role="review",
                issues_outcome="has_issues",
                # clean_outcome=None (default) — no bypass key declared
                # bypass_routes={} (default) — no bypass entries
                transitions=PhaseTransition(
                    on_success="review_analysis",
                    on_loopback="fix",
                ),
            ),
            "review_analysis": PhaseDefinition(
                drain="review_analysis",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="fix",
                ),
                loop_policy=PhaseLoopPolicy(
                    max_iterations=2,
                    iteration_state_field="review_analysis_iteration",
                ),
            ),
            "fix": PhaseDefinition(
                drain="fix",
                role="execution",
                transitions=PhaseTransition(
                    on_success="review_analysis",
                    on_loopback="review",
                ),
            ),
        },
        entry_phase="review",
        terminal_phase="complete",
    )
    state = PipelineState(phase="review")
    new_state, _ = _reduce(state, PipelineEvent.REVIEW_CLEAN, policy)
    # No clean_outcome -> falls back to on_success routing
    assert new_state.phase == "review_analysis"
    assert new_state.review_outcome is None


def test_review_issues_found_uses_policy_issues_outcome() -> None:
    """REVIEW_ISSUES_FOUND reads review_outcome label from phase_def.issues_outcome."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase="review")
    new_state, _ = _reduce(state, PipelineEvent.REVIEW_ISSUES_FOUND, policy)
    # issues_outcome="has_issues" -> review_outcome set to "has_issues"
    assert new_state.review_outcome == "has_issues"


# ---------------------------------------------------------------------------
# FULL NO-OP PIPELINE FLOW
# ---------------------------------------------------------------------------


def test_phase_handler_crash_exhausts_chain_before_failing() -> None:
    """PhaseFailureEvent(recoverable=True) must exhaust retries AND fallbacks before failing.

    This is the single most important regression guard for the bug where the pipeline
    exited on the first exception instead of going through the retry/fallback chain.
    """
    policy = _basic_pipeline_policy()
    # State with a 2-agent phase chain
    state = PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=["claude", "codex"], current_index=0, retries=0)},  # noqa: E501
    )

    # PhaseFailureEvent that simulates a handler crash
    crash_event = PhaseFailureEvent(
        phase="development",
        reason="Phase handler crashed: RuntimeError: boom",
        recoverable=True,
    )

    # Agent 0: 3 retries (retries 0->1->2->3)
    for expected_retries in range(1, 4):
        state, effects = _reduce(state, crash_event, policy)
        assert state.phase == "development"
        assert state.chain_for_phase("development").current_index == 0
        assert state.chain_for_phase("development").retries == expected_retries
        assert effects == []

    # 4th crash on agent 0: fallback to agent 1 (retries reset to 0)
    state, effects = _reduce(state, crash_event, policy)
    assert state.phase == "development"
    assert state.chain_for_phase("development").current_index == 1
    assert state.chain_for_phase("development").retries == 0
    assert effects == []

    # Agent 1: 3 more retries (retries 0->1->2->3)
    for expected_retries in range(1, 4):
        state, effects = _reduce(state, crash_event, policy)
        assert state.phase == "development"
        assert state.chain_for_phase("development").current_index == 1
        assert state.chain_for_phase("development").retries == expected_retries
        assert effects == []

    # Final crash on agent 1 (chain exhausted): recovery state
    state, effects = _reduce(state, crash_event, policy)
    assert state.phase == "failed_terminal"
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
        budget_remaining={"iteration": 2, "reviewer_pass": 1},
    )
    new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
    assert new_state.phase == "development"
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
    assert new_state.phase == "development"

    # Step 6: development → AGENT_SUCCESS → development_analysis
    state = new_state
    new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
    assert new_state.phase == "development_analysis"

    # Step 7: development_analysis → ANALYSIS_SUCCESS → development_commit
    state = new_state
    new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)
    assert new_state.phase == "development_commit"

    # Step 8: development_commit → COMMIT_SKIPPED → review (budget exhausted)
    state = new_state.with_budget_remaining("iteration", 0)
    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)
    assert new_state.phase == "review"
    assert new_state.iteration == 0

    # Step 9: review → REVIEW_CLEAN → review_commit (NOT review_analysis)
    state = new_state
    new_state, _ = _reduce(state, PipelineEvent.REVIEW_CLEAN, policy)
    assert new_state.phase == "review_commit"
    assert new_state.reviewer_pass == 0

    # Step 10: review_commit → COMMIT_SKIPPED → complete (budget exhausted)
    state = new_state.with_budget_remaining("reviewer_pass", 0)
    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)
    assert new_state.phase == "complete"
    assert new_state.reviewer_pass == 0


def test_agent_success_with_no_policy_raises_runtime_error() -> None:
    """AGENT_SUCCESS without a policy raises RuntimeError (policy is always required)."""
    import pytest  # noqa: PLC0415

    state = PipelineState(phase="development", recovery_epoch=4, last_error=None)
    with pytest.raises(RuntimeError, match="Routing requires loaded policy"):
        _reduce(state, PipelineEvent.AGENT_SUCCESS)


def test_all_workers_complete_mixed_statuses_routes_to_phase_failed() -> None:
    """ALL_WORKERS_COMPLETE with mixed statuses: last_error must name
    all failed/cancelled units sorted."""
    states = {
        "u1": WorkerState(unit_id="u1", status=WorkerStatus.SUCCEEDED),
        "u2": WorkerState(unit_id="u2", status=WorkerStatus.FAILED),
        "u3": WorkerState(unit_id="u3", status=WorkerStatus.CANCELLED),
    }
    state = PipelineState(
        phase="development",
        work_units=_make_work_units("u1", "u2", "u3"),
        worker_states=states,
    )
    policy = _basic_pipeline_policy()
    new_state, effects = _reduce(state, PipelineEvent.ALL_WORKERS_COMPLETE, policy)

    assert new_state.phase == "failed_terminal"
    assert effects == []
    error = new_state.last_error or ""
    assert "u2" in error
    assert "u3" in error
    assert error.index("u2") < error.index("u3"), (
        "Failed unit_ids must appear alphabetically: u2 before u3"
    )
