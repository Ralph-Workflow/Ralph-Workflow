"""Transition-matrix tests for canonical workflow progress accounting.

These tests lock the intended contract before refactoring:
- outer counters only change when a cycle/pass completes
- inner analysis counters track loopbacks within the current cycle/pass
- forced handoff moves to commit while keeping outer counters unchanged and preserving review flags
- skipped commits route onward without silently counting completed progress
- checkpoint-facing mirrors derive exactly from canonical PipelineState counters
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

from ralph.config.enums import (
    PHASE_COMPLETE,
    PHASE_DEVELOPMENT,
    PHASE_FAILED,
    PHASE_FIX,
    PHASE_REVIEW,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    PhaseDefinition,
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
                    on_failure=PHASE_FAILED,
                ),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                transitions=PhaseTransition(on_success=PHASE_REVIEW),
                requires_commit=True,
            ),
            PHASE_REVIEW: PhaseDefinition(
                drain="review",
                transitions=PhaseTransition(on_success="review_analysis", on_loopback=PHASE_FIX),
            ),
            "review_analysis": PhaseDefinition(
                drain="review_analysis",
                transitions=PhaseTransition(
                    on_success="review_commit",
                    on_loopback=PHASE_FIX,
                    on_failure=PHASE_FAILED,
                ),
            ),
            PHASE_FIX: PhaseDefinition(
                drain="fix",
                transitions=PhaseTransition(on_success="review_analysis", on_loopback=PHASE_REVIEW),
            ),
            "review_commit": PhaseDefinition(
                drain="review_commit",
                transitions=PhaseTransition(on_success=PHASE_COMPLETE),
                requires_commit=True,
            ),
            PHASE_COMPLETE: PhaseDefinition(
                drain="complete",
                transitions=PhaseTransition(
                    on_success=PHASE_COMPLETE,
                    on_loopback=PHASE_COMPLETE,
                ),
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


def test_review_analysis_loopback_updates_only_review_analysis_fields() -> None:
    policy = _progress_policy()
    state = PipelineState(
        phase="review_analysis",
        reviewer_pass=1,
        review_analysis_iteration=0,
        review_budget_remaining=INITIAL_REVIEW_BUDGET,
        review_issues_found=False,
    )

    new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)

    assert new_state.phase == PHASE_FIX
    assert new_state.previous_phase == "review_analysis"
    assert new_state.reviewer_pass == 1
    assert new_state.review_analysis_iteration == 1
    assert new_state.review_budget_remaining == INITIAL_REVIEW_BUDGET
    assert new_state.review_issues_found is True


def test_forced_review_analysis_handoff_preserves_outer_progress_and_marks_issue_state() -> None:
    """At max iteration, ANALYSIS_LOOPBACK routes to commit and marks issues."""
    policy = _progress_policy()
    state = PipelineState(
        phase="review_analysis",
        reviewer_pass=1,
        review_analysis_iteration=1,
        max_review_analysis_iterations=FORCED_REVIEW_ANALYSIS_ITERATION,
        review_budget_remaining=1,
        review_issues_found=False,
    )

    new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)

    assert new_state.phase == "review_commit"
    assert new_state.previous_phase == "review_analysis"
    assert new_state.reviewer_pass == 1
    assert new_state.review_analysis_iteration == FORCED_REVIEW_ANALYSIS_ITERATION
    assert new_state.review_budget_remaining == 1
    assert new_state.review_issues_found is True


def test_skipped_development_commit_preserves_outer_progress_but_resets_inner_loop() -> None:
    policy = _progress_policy()
    state = PipelineState(
        phase="development_commit",
        iteration=COMPLETED_DEVELOPMENT_CYCLES,
        development_analysis_iteration=3,
        development_budget_remaining=1,
        review_budget_remaining=1,
    )

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)

    assert new_state.phase == "planning"
    assert new_state.previous_phase == "development_commit"
    assert new_state.iteration == COMPLETED_DEVELOPMENT_CYCLES
    assert new_state.development_analysis_iteration == 0
    assert new_state.development_budget_remaining == 1


def test_skipped_review_commit_does_not_increment_outer_progress_but_resets_inner_loop() -> None:
    policy = _progress_policy()
    state = PipelineState(
        phase="review_commit",
        reviewer_pass=1,
        review_analysis_iteration=2,
        review_budget_remaining=0,
        review_issues_found=True,
    )

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SKIPPED, policy)

    assert new_state.phase == PHASE_COMPLETE
    assert new_state.previous_phase == "review_commit"
    assert new_state.reviewer_pass == 1
    assert new_state.review_analysis_iteration == 0
    assert new_state.review_issues_found is True


def test_checkpoint_builder_derives_progress_mirrors_from_pipeline_state() -> None:
    state = PipelineState(
        phase=PHASE_REVIEW,
        iteration=1,
        reviewer_pass=COMPLETED_REVIEW_PASSES,
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
