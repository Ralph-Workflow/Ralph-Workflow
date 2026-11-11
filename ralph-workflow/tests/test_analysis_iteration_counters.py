from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from ralph.pipeline.events import AnalysisDecisionEvent, PipelineEvent
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

_ANALYSIS_LOOP_COUNTERS = {
    "development_analysis_iteration": LoopCounterConfig(default_max=3),
    "review_analysis_iteration": LoopCounterConfig(default_max=2),
}

_COMMIT_BUDGET_CAPS = {"iteration": 3, "reviewer_pass": 2}

_DECISION_COMPLETED = AnalysisDecisionEvent(phase="development_analysis", decision="completed")
_DECISION_REQUEST_CHANGES = AnalysisDecisionEvent(
    phase="development_analysis", decision="request_changes"
)


def _analysis_phases() -> dict[str, PhaseDefinition]:
    return {
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
                on_success="review_analysis",
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
    }


def _post_commit_routes() -> list[PostCommitRoute]:
    return [
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
    ]


def _dev_analysis_policy(*, with_loop_counters: bool) -> PipelinePolicy:
    return PipelinePolicy(
        phases=_analysis_phases(),
        entry_phase="development",
        terminal_phase="complete",
        post_commit_routes=_post_commit_routes(),
        loop_counters=dict(_ANALYSIS_LOOP_COUNTERS) if with_loop_counters else {},
    )


def _review_commit_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "review": PhaseDefinition(
                drain="review",
                transitions=PhaseTransition(
                    on_success="review_commit",
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
        entry_phase="review",
        terminal_phase="complete",
    )


@dataclass(frozen=True, eq=False)
class _CounterCase:
    policy: str
    phase: str
    event: PipelineEvent | AnalysisDecisionEvent
    loop_iterations: dict[str, int] = field(default_factory=dict)
    budget_caps: dict[str, int] | None = None
    expected_phase: str | None = None
    expected_outer_progress: tuple[str, int] | None = None
    expected_loop_iteration: tuple[str, int] | None = None
    expected_loop_iteration_at_cap: str | None = None


_CASES: tuple[_CounterCase, ...] = (
    _CounterCase(
        policy="analysis_with_caps",
        phase="development_analysis",
        event=_DECISION_COMPLETED,
        loop_iterations={"development_analysis_iteration": 2},
        expected_phase="development_commit",
        expected_loop_iteration=("development_analysis_iteration", 0),
    ),
    _CounterCase(
        policy="analysis_with_caps",
        phase="development_analysis",
        event=_DECISION_COMPLETED,
        loop_iterations={"development_analysis_iteration": 0},
        expected_phase="development_commit",
    ),
    _CounterCase(
        policy="analysis_with_caps",
        phase="development_analysis",
        event=_DECISION_REQUEST_CHANGES,
        loop_iterations={"development_analysis_iteration": 0},
        expected_phase="development",
        expected_loop_iteration=("development_analysis_iteration", 1),
    ),
    _CounterCase(
        policy="analysis_with_caps",
        phase="development_analysis",
        event=_DECISION_REQUEST_CHANGES,
        loop_iterations={"development_analysis_iteration": 2},
        expected_phase="development",
        expected_loop_iteration_at_cap="development_analysis_iteration",
    ),
    _CounterCase(
        policy="analysis_with_caps",
        phase="development_analysis",
        event=PipelineEvent.ANALYSIS_SUCCESS,
        loop_iterations={"development_analysis_iteration": 2},
        expected_phase="development_commit",
        expected_loop_iteration=("development_analysis_iteration", 0),
    ),
    _CounterCase(
        policy="analysis_with_caps",
        phase="review_analysis",
        event=PipelineEvent.ANALYSIS_SUCCESS,
        loop_iterations={"review_analysis_iteration": 1},
        expected_phase="review_commit",
        expected_loop_iteration=("review_analysis_iteration", 0),
    ),
    _CounterCase(
        policy="analysis_with_caps",
        phase="development_analysis",
        event=PipelineEvent.ANALYSIS_LOOPBACK,
        loop_iterations={"development_analysis_iteration": 2},
        expected_phase="development",
        expected_loop_iteration_at_cap="development_analysis_iteration",
    ),
    _CounterCase(
        policy="analysis_with_caps",
        phase="development_analysis",
        event=PipelineEvent.ANALYSIS_LOOPBACK,
        loop_iterations={"development_analysis_iteration": 0},
        expected_phase="development",
        expected_loop_iteration=("development_analysis_iteration", 1),
    ),
    _CounterCase(
        policy="analysis_without_caps",
        phase="development_commit",
        event=PipelineEvent.COMMIT_SUCCESS,
        budget_caps=dict(_COMMIT_BUDGET_CAPS),
        expected_outer_progress=("iteration", 1),
    ),
    _CounterCase(
        policy="analysis_without_caps",
        phase="development_commit",
        event=PipelineEvent.COMMIT_SUCCESS,
        loop_iterations={"development_analysis_iteration": 3},
        budget_caps=dict(_COMMIT_BUDGET_CAPS),
        expected_outer_progress=("iteration", 1),
        expected_loop_iteration=("development_analysis_iteration", 0),
    ),
    _CounterCase(
        policy="analysis_with_caps",
        phase="review_analysis",
        event=PipelineEvent.ANALYSIS_LOOPBACK,
        loop_iterations={"review_analysis_iteration": 1},
        expected_phase="fix",
        expected_loop_iteration_at_cap="review_analysis_iteration",
    ),
    _CounterCase(
        policy="analysis_with_caps",
        phase="review_analysis",
        event=PipelineEvent.ANALYSIS_LOOPBACK,
        loop_iterations={"review_analysis_iteration": 0},
        expected_phase="fix",
        expected_loop_iteration=("review_analysis_iteration", 1),
        expected_outer_progress=("reviewer_pass", 0),
    ),
    _CounterCase(
        policy="review_commit",
        phase="review_commit",
        event=PipelineEvent.COMMIT_SUCCESS,
        budget_caps=dict(_COMMIT_BUDGET_CAPS),
        expected_outer_progress=("reviewer_pass", 1),
    ),
    _CounterCase(
        policy="analysis_without_caps",
        phase="review_commit",
        event=PipelineEvent.COMMIT_SUCCESS,
        loop_iterations={"review_analysis_iteration": 2},
        budget_caps=dict(_COMMIT_BUDGET_CAPS),
        expected_outer_progress=("reviewer_pass", 1),
        expected_loop_iteration=("review_analysis_iteration", 0),
    ),
)

_CASE_IDS: tuple[str, ...] = (
    "analysis_iteration_new_analysis_decision_event_completed__completed_resets_loop_counter_to_zero",
    "analysis_iteration_new_analysis_decision_event_completed__completed_routes_to_commit_phase",
    "analysis_iteration_new_analysis_decision_event_request_changes__request_changes_increments_loop_counter",
    "analysis_iteration_new_analysis_decision_event_request_changes__request_changes_at_cap_clamps_to_max",
    "analysis_iteration_new_analysis_success_resets_counters__dev_analysis_success_resets_dev_analysis_iteration",
    "analysis_iteration_new_analysis_success_resets_counters__review_analysis_success_resets_review_analysis_iteration",
    "analysis_iteration_new_dev_analysis_loopback_at_max_routes_to_development__dev_analysis_loopback_at_max_routes_to_development",
    "analysis_iteration_new_dev_analysis_loopback_increments_continuation_counter__dev_analysis_loopback_increments_continuation_counter",
    "analysis_iteration_new_dev_commit_success_increments_iteration__dev_commit_success_increments_iteration",
    "analysis_iteration_new_dev_commit_success_resets_dev_analysis_iteration__dev_commit_success_resets_dev_analysis_iteration",
    "analysis_iteration_new_review_analysis_loopback_at_max_routes_to_fix__review_analysis_loopback_at_max_routes_to_fix",
    "analysis_iteration_new_review_analysis_loopback_increments_continuation_counter__review_analysis_loopback_increments_continuation_counter",
    "analysis_iteration_new_review_commit_success_increments_reviewer_pass__review_commit_success_increments_reviewer_pass",
    "analysis_iteration_new_review_commit_success_resets_review_analysis_iteration__review_commit_success_resets_review_analysis_iteration",
)


def _build_policy(kind: str) -> PipelinePolicy:
    if kind == "analysis_with_caps":
        return _dev_analysis_policy(with_loop_counters=True)
    if kind == "analysis_without_caps":
        return _dev_analysis_policy(with_loop_counters=False)
    return _review_commit_policy()


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_analysis_iteration_counter(case: _CounterCase) -> None:
    policy = _build_policy(case.policy)
    state = PipelineState(
        phase=case.phase,
        loop_iterations=dict(case.loop_iterations),
        budget_caps=dict(case.budget_caps or {}),
    )
    new_state, _effects = reducer_reduce(state, case.event, policy)

    if case.expected_phase is not None:
        assert new_state.phase == case.expected_phase
    if case.expected_outer_progress is not None:
        counter, expected = case.expected_outer_progress
        assert new_state.get_outer_progress(counter) == expected
    if case.expected_loop_iteration is not None:
        counter, expected = case.expected_loop_iteration
        assert new_state.get_loop_iteration(counter) == expected
    if case.expected_loop_iteration_at_cap is not None:
        counter = case.expected_loop_iteration_at_cap
        assert new_state.get_loop_iteration(counter) == policy.loop_counters[counter].default_max
