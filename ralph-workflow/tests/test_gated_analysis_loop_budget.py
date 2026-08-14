"""A gated analysis loop must run every pass its cap declares.

``development_analysis`` is the one phase whose entry is guarded by an
invocation gate, and the gate charges its loop counter on entry while every
other charge site charges on loopback, after the pass. The skip predicate is
written for the after-the-pass meaning ("completed loopbacks"), so the entry
charge made the budget look spent one pass early: a declared cap of five
yielded four invocations, and the operator was billed a counter unit for a
pass that never ran.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
_COUNTER = "development_analysis_iteration"
# The gate admits analysis outright for these result statuses, so the test does
# not have to synthesize phase timings to clear the elapsed-seconds threshold.
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
    """Entering with cap-1 passes behind it must run the cap'th pass."""
    cap = _cap()

    next_state, _effects = reducer_reduce(
        _commit_with_completed_passes(cap - 1),
        PipelineEvent.COMMIT_SUCCESS,
        _pipeline(),
    )

    assert next_state.phase == "development_analysis"


def test_a_spent_analysis_budget_is_not_charged_again_on_the_way_past() -> None:
    """The bypass must not bill a counter unit for the pass it skips."""
    cap = _cap()

    next_state, _effects = reducer_reduce(
        _commit_with_completed_passes(cap),
        PipelineEvent.COMMIT_SUCCESS,
        _pipeline(),
    )

    assert next_state.phase == "development_final_commit_cleanup"


def test_the_gated_loop_runs_exactly_its_declared_number_of_passes() -> None:
    """The premise end to end: a cap of N yields N analysis invocations.

    Counting the passes rather than asserting one transition is what pins the
    off-by-one: the loop still terminated before, just one pass short, so every
    boundary-free test stayed green.
    """
    cap = _cap()
    state = _commit_with_completed_passes(0)
    invocations = 0

    for _ in range(cap * 2 + 2):
        state, _effects = reducer_reduce(state, PipelineEvent.COMMIT_SUCCESS, _pipeline())
        if state.phase != "development_analysis":
            break
        invocations += 1
        # The pass runs and asks for more work, returning through development
        # and its commit exactly as the live loop does.
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
