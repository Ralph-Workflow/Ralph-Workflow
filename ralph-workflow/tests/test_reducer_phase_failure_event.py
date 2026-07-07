"""Unit tests for the pure pipeline reducer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from ralph.pipeline.events import (
    PhaseFailureEvent,
    PipelineEvent,
)
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.models import (
    BudgetCounterConfig,
    LoopCounterConfig,
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseTransition,
    PhaseWorkflowFallback,
    PipelinePolicy,
    PostCommitRoute,
    PostCommitRouteWhen,
    RecoveryPolicy,
)
from ralph.recovery.classifier import FailureCategory
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions

DEVELOPMENT_ANALYSIS_TWO_RUN_CAP = 2

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
                loop_policy=PhaseLoopPolicy(iteration_state_field="development_analysis_iteration"),
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
        loop_counters={
            "development_analysis_iteration": LoopCounterConfig(default_max=3),
            "review_analysis_iteration": LoopCounterConfig(default_max=2),
        },
        budget_counters={
            "iteration": BudgetCounterConfig(default_max=5),
            "reviewer_pass": BudgetCounterConfig(default_max=1),
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


def _policy_with_planning_analysis() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                role="execution",
                transitions=PhaseTransition(on_success="planning_analysis"),
            ),
            "planning_analysis": PhaseDefinition(
                drain="planning_analysis",
                role="analysis",
                transitions=PhaseTransition(on_success="development", on_loopback="planning"),
                loop_policy=PhaseLoopPolicy(iteration_state_field="planning_analysis_iteration"),
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
                loop_policy=PhaseLoopPolicy(iteration_state_field="development_analysis_iteration"),
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
        loop_counters={
            "planning_analysis_iteration": LoopCounterConfig(default_max=10),
            "development_analysis_iteration": LoopCounterConfig(default_max=3),
            "review_analysis_iteration": LoopCounterConfig(default_max=2),
        },
        budget_counters={
            "iteration": BudgetCounterConfig(default_max=5),
            "reviewer_pass": BudgetCounterConfig(default_max=1),
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
            phase_chains={
                "development": AgentChainState(agents=["claude"], current_index=0, retries=0)
            },
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
            phase_chains={
                "development": AgentChainState(
                    agents=["claude", "opencode"], current_index=0, retries=3
                )
            },
        )
        event = PhaseFailureEvent(phase="development", reason="missing artifact", recoverable=True)
        new_state, effects = _reduce(state, event)
        assert new_state.chain_for_phase("development").current_index == 1
        assert new_state.chain_for_phase("development").retries == 0
        assert effects == []

    def test_phase_failure_skip_same_agent_retries_falls_back_immediately(
        self,
    ) -> None:
        """Context-exhausted agents should advance to the next agent without retries."""
        state = PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(
                    agents=["pi/zai/glm-5.2", "codex"], current_index=0, retries=0
                )
            },
        )
        event = PhaseFailureEvent(
            phase="development",
            reason="Pi context length exhausted",
            recoverable=True,
            skip_same_agent_retries=True,
        )
        new_state, effects = _reduce(state, event)
        chain = new_state.chain_for_phase("development")
        assert chain.current_index == 1
        assert chain.retries == 0
        assert new_state.metrics.total_fallbacks == 1
        assert new_state.metrics.total_retries == 0
        assert effects == []

    def test_phase_failure_recoverable_with_single_agent_after_3_retries_enters_recovery(
        self,
    ) -> None:
        """Single-agent chain exhaustion should enter recovery without exit effects."""
        state = PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(agents=["claude"], current_index=0, retries=3)
            },
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
            phase_chains={
                "development": AgentChainState(agents=["claude"], current_index=0, retries=3)
            },
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
            phase_chains={
                "review": AgentChainState(agents=["reviewer"], current_index=0, retries=3)
            },
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

    def test_commit_failure_does_not_reuse_stale_last_error(self) -> None:
        policy = PipelinePolicy(
            phases={
                "development_final_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "failed_terminal": PhaseDefinition(
                    drain="failed_terminal",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed_terminal"),
                ),
            },
            entry_phase="development_final_commit",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="failed_terminal"),
        )
        state = PipelineState(
            phase="development_final_commit",
            last_error="Artifact validation fault: stale proof mismatch",
        )

        new_state, effects = _reduce(state, PipelineEvent.COMMIT_FAILURE, policy)

        assert new_state.phase == "failed_terminal"
        assert new_state.last_error == "development_final_commit: Commit failed"
        assert effects == []

    def test_phase_failure_with_recovery_uses_actual_failure_category_prefix(self) -> None:
        state = PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(agents=["claude"], current_index=0, retries=0)
            },
        )
        event = PhaseFailureEvent(
            phase="development",
            reason="something unclear happened",
            recoverable=True,
            failure_category=FailureCategory.AMBIGUOUS,
        )
        controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))

        new_state, effects = reducer_reduce(state, event, recovery=controller)

        assert new_state.phase == "development"
        assert effects == []
        assert new_state.last_failure_category == FailureCategory.AMBIGUOUS
        assert new_state.last_error is not None
        assert "Ambiguous fault" in new_state.last_error
        assert "Artifact validation fault" not in new_state.last_error
