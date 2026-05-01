"""Transition-matrix tests for canonical workflow progress accounting.

These tests lock the intended contract before refactoring:
- outer counters only change when a cycle/pass completes
- inner analysis counters track loopbacks within the current cycle/pass
- capped correction routing preserves outer counters and review flags
- skipped commits route onward without silently counting completed progress
- checkpoint-facing mirrors derive exactly from canonical PipelineState counters
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

import pytest

from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.progress import advance_phase, apply_commit_outcome
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    BudgetCounterConfig,
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseTransition,
    PipelinePolicy,
    PostCommitRoute,
    PostCommitRouteWhen,
)

if TYPE_CHECKING:
    from ralph.pipeline.effects import Effect

checkpoint_module = import_module("ralph.checkpoint")
CheckpointBuilder = checkpoint_module.CheckpointBuilder
RunContext = checkpoint_module.RunContext

INITIAL_REVIEW_BUDGET = 2
FORCED_REVIEW_ANALYSIS_ITERATION = 2
COMPLETED_DEVELOPMENT_CYCLES = 2
COMPLETED_REVIEW_PASSES = 2


def _reduce(
    state: PipelineState,
    event: object,
    policy: PipelinePolicy | None = None,
) -> tuple[PipelineState, list[Effect]]:
    return reducer_reduce(state, cast("Any", event), policy)


def _progress_policy() -> PipelinePolicy:
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
                transitions=PhaseTransition(
                    on_success="development_commit",
                    on_loopback="development",
                    on_failure=None,
                ),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                transitions=PhaseTransition(on_success="review"),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="iteration",
                    loop_resets=["development_analysis_iteration"],
                ),
            ),
            "review": PhaseDefinition(
                drain="review",
                transitions=PhaseTransition(on_success="review_analysis", on_loopback="fix"),
            ),
            "review_analysis": PhaseDefinition(
                drain="review_analysis",
                transitions=PhaseTransition(
                    on_success="review_commit",
                    on_loopback="fix",
                    on_failure=None,
                ),
                loop_policy=PhaseLoopPolicy(
                    max_iterations=2,
                    iteration_state_field="review_analysis_iteration",
                    loopback_review_outcome="has_issues",
                ),
            ),
            "fix": PhaseDefinition(
                drain="fix",
                transitions=PhaseTransition(on_success="review_analysis", on_loopback="review"),
            ),
            "review_commit": PhaseDefinition(
                drain="review_commit",
                role="commit",
                transitions=PhaseTransition(on_success="complete"),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="reviewer_pass",
                    loop_resets=["review_analysis_iteration"],
                ),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="complete",
                ),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
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
                when=PostCommitRouteWhen(phase="review_commit", budget_state="remaining"),
                target="review",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="review_commit", budget_state="exhausted"),
                target="complete",
            ),
        ],
    )


def test_review_analysis_loopback_updates_only_review_analysis_fields() -> None:
    policy = _progress_policy()
    state = PipelineState(
        phase="review_analysis",
        outer_progress={"reviewer_pass": 1},
        loop_iterations={"review_analysis_iteration": 0},
        budget_remaining={"reviewer_pass": INITIAL_REVIEW_BUDGET},
        )

    new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)

    assert new_state.phase == "fix"
    assert new_state.previous_phase == "review_analysis"
    assert new_state.get_outer_progress("reviewer_pass") == 1
    assert new_state.get_loop_iteration("review_analysis_iteration") == 1
    assert new_state.get_budget_remaining("reviewer_pass") == INITIAL_REVIEW_BUDGET
    assert new_state.review_outcome is not None


def test_capped_review_analysis_loopback_preserves_outer_progress_and_marks_issue_state() -> None:
    """At max iteration, ANALYSIS_LOOPBACK still routes to fix and marks issues."""
    policy = _progress_policy()
    state = PipelineState(
        phase="review_analysis",
        outer_progress={"reviewer_pass": 1},
        loop_iterations={"review_analysis_iteration": 1},
        loop_caps={"review_analysis_iteration": FORCED_REVIEW_ANALYSIS_ITERATION},
        budget_remaining={"reviewer_pass": 1},
        )

    new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)

    assert new_state.phase == "fix"
    assert new_state.previous_phase == "review_analysis"
    assert new_state.get_outer_progress("reviewer_pass") == 1
    assert (
        new_state.get_loop_iteration("review_analysis_iteration")
        == FORCED_REVIEW_ANALYSIS_ITERATION
    )
    assert new_state.get_budget_remaining("reviewer_pass") == 1
    assert new_state.review_outcome is not None


def test_skipped_development_commit_preserves_outer_progress_but_resets_inner_loop() -> None:
    policy = _progress_policy()
    state = PipelineState(
        phase="development_commit",
        outer_progress={"iteration": COMPLETED_DEVELOPMENT_CYCLES},
        loop_iterations={"development_analysis_iteration": 3},
        budget_remaining={"iteration": 1, "reviewer_pass": 1},
    )

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)

    assert new_state.phase == "review"
    assert new_state.previous_phase == "development_commit"
    assert new_state.get_outer_progress("iteration") == COMPLETED_DEVELOPMENT_CYCLES
    assert new_state.get_loop_iteration("development_analysis_iteration") == 0
    assert new_state.get_budget_remaining("iteration") == 0


def test_skipped_review_commit_does_not_increment_outer_progress_but_resets_inner_loop() -> None:
    policy = _progress_policy()
    state = PipelineState(
        phase="review_commit",
        outer_progress={"reviewer_pass": 1},
        loop_iterations={"review_analysis_iteration": 2},
        budget_remaining={"reviewer_pass": 1},
        review_outcome="has_issues",
    )

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)

    assert new_state.phase == "complete"
    assert new_state.previous_phase == "review_commit"
    assert new_state.get_outer_progress("reviewer_pass") == 1
    assert new_state.get_loop_iteration("review_analysis_iteration") == 0
    assert new_state.get_budget_remaining("reviewer_pass") == 0
    assert new_state.review_outcome is not None


def test_commit_budget_routes_use_post_commit_policy_after_budget_is_consumed() -> None:
    policy = _progress_policy()
    state = PipelineState(
        phase="development_commit",
        budget_remaining={"iteration": 1, "reviewer_pass": 1},
    )

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert new_state.phase == "review"
    assert new_state.get_outer_progress("iteration") == 1
    assert new_state.get_budget_remaining("iteration") == 0


def test_checkpoint_builder_derives_progress_mirrors_from_pipeline_state() -> None:
    state = PipelineState(
        phase="review",
        outer_progress={"iteration": 1, "reviewer_pass": COMPLETED_REVIEW_PASSES},
    )
    stale_context = RunContext(
        run_id="resume-run",
        parent_run_id="parent-run",
        resume_count=3,
        actual_developer_runs=7,
        actual_reviewer_runs=9,
    )

    payload = CheckpointBuilder.new().state(state).run_context(stale_context).build()

    assert payload.run_context.actual_developer_runs == 1
    assert payload.run_context.actual_reviewer_runs == COMPLETED_REVIEW_PASSES


def test_checkpoint_builder_derives_progress_from_policy_with_custom_counter_names() -> None:
    """When policy is supplied, checkpoint mirrors use BFS-ordered tracked counters.

    Proves the runtime never hardcodes canonical counter names: a renamed policy
    ('design_cycles' and 'audit_passes') still populates actual_developer_runs and
    actual_reviewer_runs correctly via policy-driven BFS resolution.
    """
    custom_policy = PipelinePolicy(
        entry_phase="design",
        terminal_phase="done",
        budget_counters={
            "design_cycles": BudgetCounterConfig(
                tracks_budget=True, description="design cycle counter", default_max=5
            ),
            "audit_passes": BudgetCounterConfig(
                tracks_budget=True, description="audit pass counter", default_max=2
            ),
        },
        phases={
            "design": PhaseDefinition(
                drain="design",
                role="execution",
                transitions=PhaseTransition(on_success="design_commit"),
            ),
            "design_commit": PhaseDefinition(
                drain="design_commit",
                role="commit",
                transitions=PhaseTransition(on_success="audit"),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="design_cycles",
                    loop_resets=[],
                ),
            ),
            "audit": PhaseDefinition(
                drain="audit",
                role="execution",
                transitions=PhaseTransition(on_success="audit_commit"),
            ),
            "audit_commit": PhaseDefinition(
                drain="audit_commit",
                role="commit",
                transitions=PhaseTransition(on_success="done"),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="audit_passes",
                    loop_resets=[],
                ),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done"),
            ),
        },
        post_commit_routes=[
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="design_commit", budget_state="remaining"),
                target="design",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="design_commit", budget_state="exhausted"),
                target="audit",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="design_commit", budget_state="no_review"),
                target="done",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="audit_commit", budget_state="remaining"),
                target="audit",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="audit_commit", budget_state="exhausted"),
                target="done",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="audit_commit", budget_state="no_review"),
                target="done",
            ),
        ],
    )
    state = PipelineState(
        phase="audit",
        outer_progress={"design_cycles": 3, "audit_passes": 1},
    )
    context = RunContext.new()

    payload = (
        CheckpointBuilder.new()
        .state(state)
        .run_context(context)
        .pipeline_policy(custom_policy)
        .build()
    )

    # 'design_cycles' is the first tracked counter in BFS commit order → developer runs
    assert payload.run_context.actual_developer_runs == 3  # noqa: PLR2004
    # 'audit_passes' is the second tracked counter → reviewer runs
    assert payload.run_context.actual_reviewer_runs == 1


class TestProgressPolicyRequired:
    """Commit-role phase transitions require policy to be provided.

    These tests enforce that apply_commit_outcome and advance_phase raise ValueError
    when called for a commit-role phase without providing the PipelinePolicy.
    """

    def _commit_phase_policy(self) -> PipelinePolicy:
        return PipelinePolicy(
            phases={
                "feature_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="iteration",
                        loop_resets=["development_analysis_iteration"],
                    ),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="feature_commit",
            terminal_phase="complete",
        )

    def test_apply_commit_outcome_requires_policy(self) -> None:
        """apply_commit_outcome raises ValueError when policy=None for a commit-role phase."""
        state = PipelineState(
            phase="feature_commit",
            loop_iterations={"development_analysis_iteration": 0},
        )
        advanced_state = state.copy_with(phase="complete")

        with pytest.raises(ValueError, match="requires PipelinePolicy"):
            apply_commit_outcome(state, advanced_state, skipped=False, policy=None)

    def test_apply_commit_outcome_with_policy_does_not_raise(self) -> None:
        """apply_commit_outcome succeeds when policy is provided for a commit-role phase."""
        policy = self._commit_phase_policy()
        state = PipelineState(
            phase="feature_commit",
            outer_progress={"iteration": 1},
            loop_iterations={"development_analysis_iteration": 0},
        )
        advanced_state = state.copy_with(phase="complete")

        result = apply_commit_outcome(state, advanced_state, skipped=False, policy=policy)
        # skipped=False increments iteration per commit_policy.increments_counter="iteration"
        assert result.get_outer_progress("iteration") == state.get_outer_progress("iteration") + 1

    def test_advance_phase_requires_policy_for_commit_target(self) -> None:
        """advance_phase raises ValueError when policy=None for any target phase."""
        state = PipelineState(phase="planning")

        with pytest.raises(ValueError, match="requires PipelinePolicy"):
            advance_phase(state, "feature_commit", policy=None)

    def test_advance_phase_with_policy_does_not_raise(self) -> None:
        """advance_phase succeeds when policy is provided."""
        policy = self._commit_phase_policy()
        state = PipelineState(phase="planning")

        result = advance_phase(state, "feature_commit", policy=policy)
        assert result.phase == "feature_commit"
