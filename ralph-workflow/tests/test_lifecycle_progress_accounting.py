"""Progress tests for lifecycle-owned accounting metadata."""

from __future__ import annotations

from importlib import import_module

from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.progress import derive_run_context_progress
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    BudgetCounterConfig,
    LifecyclePhasePolicy,
    LoopCounterConfig,
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseTransition,
    PipelinePolicy,
    PostCommitRoute,
    PostCommitRouteWhen,
)

checkpoint_module = import_module("ralph.checkpoint")
RunContext = checkpoint_module.RunContext


def _lifecycle_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                transitions=PhaseTransition(on_success="development"),
            ),
            "development": PhaseDefinition(
                drain="development",
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
                transitions=PhaseTransition(on_success="complete", on_failure="failed_terminal"),
                commit_policy=PhaseCommitPolicy(
                    requires_artifact=True,
                    skipped_advances_progress=False,
                ),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="failed_terminal",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(
                    on_success="failed_terminal",
                    on_loopback="failed_terminal",
                ),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
        loop_counters={
            "development_analysis_iteration": LoopCounterConfig(default_max=3),
        },
        budget_counters={
            "iteration": BudgetCounterConfig(
                tracks_budget=True,
                description="developer iteration counter",
                default_max=2,
            ),
        },
        lifecycle_phases={
            "development_commit": LifecyclePhasePolicy(
                lifecycle_name="developer_iteration",
                completion_block="development_commit",
                increments_counter="iteration",
                loop_resets=["development_analysis_iteration"],
                before_complete=["development_commit_cleanup"],
                after_complete=[],
            )
        },
        post_commit_routes=[
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="development_commit", budget_state="remaining"),
                target="planning",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="development_commit", budget_state="exhausted"),
                target="complete",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="development_commit", budget_state="no_review"),
                target="complete",
            ),
        ],
    )


def test_commit_success_uses_lifecycle_metadata_for_budget_progress() -> None:
    policy = _lifecycle_policy()
    state = PipelineState(
        phase="development_commit",
        outer_progress={"iteration": 0},
        loop_iterations={"development_analysis_iteration": 2},
        budget_caps={"iteration": 2},
    )

    new_state, _ = reducer_reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert new_state.phase == "planning"
    assert new_state.previous_phase == "development_commit"
    assert new_state.get_outer_progress("iteration") == 1
    assert new_state.get_loop_iteration("development_analysis_iteration") == 0
    assert new_state.get_budget_remaining("iteration") == 1


def test_commit_skip_still_counts_when_lifecycle_owns_completion() -> None:
    policy = _lifecycle_policy()
    state = PipelineState(
        phase="development_commit",
        outer_progress={"iteration": 0},
        loop_iterations={"development_analysis_iteration": 1},
        budget_caps={"iteration": 1},
    )

    new_state, _ = reducer_reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)

    assert new_state.phase == "complete"
    assert new_state.get_outer_progress("iteration") == 1
    assert new_state.get_loop_iteration("development_analysis_iteration") == 0
    assert new_state.get_budget_remaining("iteration") == 0


def test_run_context_progress_uses_lifecycle_phase_order_not_commit_policy_counter() -> None:
    policy = _lifecycle_policy()
    state = PipelineState(
        phase="development_commit",
        outer_progress={"iteration": 1},
        budget_caps={"iteration": 2},
    )
    run_context = RunContext.new()

    derived = derive_run_context_progress(state, run_context, policy)

    assert derived.actual_developer_runs == 1
    assert derived.actual_reviewer_runs == 0
