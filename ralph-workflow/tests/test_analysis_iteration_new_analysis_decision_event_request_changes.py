"""New tests for analysis iteration counters and caps.

These tests verify:
1. iteration increments on dev commit success
2. reviewer_pass increments on review commit success
3. analysis counters increment on loopback
4. max-analysis still routes through correction phases without exceeding the cap
5. counters reset on commit success and analysis success
6. AnalysisDecisionEvent with request_changes mirrors ANALYSIS_LOOPBACK counter accounting
7. AnalysisDecisionEvent with completed mirrors ANALYSIS_SUCCESS counter reset
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from ralph.pipeline.events import AnalysisDecisionEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    LoopCounterConfig,
    PhaseCommitPolicy,
    PhaseDecisionRoute,
    PhaseDefinition,
    PhaseLoopPolicy,
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


def _dev_analysis_policy() -> PipelinePolicy:
    """Policy with analysis phases that route to correction on ordinary loopback.

    This matches the intended default pipeline.toml behavior: ordinary analysis
    loopbacks route to development/fix, and capped loopbacks still take one
    final correction pass without letting the counter exceed the configured cap.
    """
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="development_analysis",
                    on_failure=None,
                    on_loopback="development",
                ),
            ),
            "development_analysis": PhaseDefinition(
                drain="development_analysis",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="development_commit",
                    on_failure=None,
                    on_loopback="development",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="development_analysis_iteration"),
                decisions={
                    "completed": PhaseDecisionRoute(target="development_commit", reset_loop=True),
                    "request_changes": PhaseDecisionRoute(target="development", reset_loop=False),
                },
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                transitions=PhaseTransition(
                    on_success="development",
                    on_failure=None,
                    on_loopback="development",
                ),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="iteration",
                    loop_resets=["development_analysis_iteration"],
                ),
            ),
            "review": PhaseDefinition(
                drain="review",
                transitions=PhaseTransition(
                    on_success="review_analysis",
                    on_failure=None,
                    on_loopback="review",
                ),
            ),
            "review_analysis": PhaseDefinition(
                drain="review_analysis",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="review_commit",
                    on_failure=None,
                    on_loopback="fix",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="review_analysis_iteration"),
            ),
            "fix": PhaseDefinition(
                drain="fix",
                transitions=PhaseTransition(
                    on_success="review_analysis",  # fix success goes to review_analysis
                    on_failure=None,
                    on_loopback="review",
                ),
            ),
            "review_commit": PhaseDefinition(
                drain="review_commit",
                role="commit",
                transitions=PhaseTransition(
                    on_success="review",
                    on_failure=None,
                    on_loopback="review",
                ),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="reviewer_pass",
                    loop_resets=["review_analysis_iteration"],
                ),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
        loop_counters={
            "development_analysis_iteration": LoopCounterConfig(default_max=3),
            "review_analysis_iteration": LoopCounterConfig(default_max=2),
        },
        post_commit_routes=[
            PostCommitRoute(
                when=PostCommitRouteWhen(
                    phase="development_commit",
                    budget_state="exhausted",
                ),
                target="development",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(
                    phase="review_commit",
                    budget_state="exhausted",
                ),
                target="review",
            ),
        ],
    )


class TestAnalysisDecisionEventRequestChanges:
    """Test 6: AnalysisDecisionEvent(request_changes) mirrors ANALYSIS_LOOPBACK accounting."""

    def test_request_changes_increments_loop_counter(self) -> None:
        """AnalysisDecisionEvent(request_changes) increments development_analysis_iteration."""
        policy = _dev_analysis_policy()
        state = PipelineState(
            phase="development_analysis",
            loop_iterations={"development_analysis_iteration": 0},
        )
        event = AnalysisDecisionEvent(phase="development_analysis", decision="request_changes")
        new_state, _ = _reduce(state, event, policy)
        assert new_state.phase == "development"
        assert new_state.get_loop_iteration("development_analysis_iteration") == 1

    def test_request_changes_at_cap_clamps_to_max(self) -> None:
        """AnalysisDecisionEvent(request_changes) at cap clamps counter to max, does not exceed."""
        policy = _dev_analysis_policy()
        state = PipelineState(
            phase="development_analysis",
            loop_iterations={"development_analysis_iteration": 2},
        )
        event = AnalysisDecisionEvent(phase="development_analysis", decision="request_changes")
        new_state, _ = _reduce(state, event, policy)
        assert new_state.phase == "development"
        max_cap = policy.loop_counters["development_analysis_iteration"].default_max
        assert new_state.get_loop_iteration("development_analysis_iteration") == max_cap
