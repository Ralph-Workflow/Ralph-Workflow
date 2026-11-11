from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
_COUNTER = "development_analysis_iteration"
_ALWAYS_INVOKE_STATUS = "partial"


@lru_cache(maxsize=1)
def _pipeline() -> object:
    return load_policy(_DEFAULTS_DIR).pipeline


def _cap() -> int:
    return _pipeline().loop_counters[_COUNTER].default_max


def _commit_with_completed_passes(completed: int) -> PipelineState:
    return PipelineState(
        phase="development_commit",
        budget_caps={"iteration": 5},
        loop_iterations={_COUNTER: completed},
        last_execution_result_status=_ALWAYS_INVOKE_STATUS,
    )


def test_the_final_declared_analysis_pass_is_still_invoked() -> None:
    cap = _cap()

    next_state, _effects = reducer_reduce(
        _commit_with_completed_passes(cap - 1),
        PipelineEvent.COMMIT_SUCCESS,
        _pipeline(),
    )

    assert next_state.phase == "development_analysis"


def test_a_spent_analysis_budget_is_not_charged_again_on_the_way_past() -> None:
    cap = _cap()

    next_state, _effects = reducer_reduce(
        _commit_with_completed_passes(cap),
        PipelineEvent.COMMIT_SUCCESS,
        _pipeline(),
    )

    assert next_state.phase == "development_final_commit_review"


def test_the_gated_loop_runs_exactly_its_declared_number_of_passes() -> None:
    cap = _cap()
    state = _commit_with_completed_passes(0)
    invocations = 0

    for _ in range(cap * 2 + 2):
        state, _effects = reducer_reduce(state, PipelineEvent.COMMIT_SUCCESS, _pipeline())
        if state.phase != "development_analysis":
            break
        invocations += 1
        state, _effects = reducer_reduce(
            state,
            PipelineEvent.ANALYSIS_LOOPBACK,
            _pipeline(),
        )
        state = state.copy_with(
            phase="development_commit",
            last_execution_result_status=_ALWAYS_INVOKE_STATUS,
        )

    assert invocations == cap
