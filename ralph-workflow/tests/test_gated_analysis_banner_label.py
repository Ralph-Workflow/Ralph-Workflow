from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ralph.pipeline import progress
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


@lru_cache(maxsize=1)
def _pipeline() -> object:
    return load_policy(_DEFAULTS_DIR).pipeline


def _label(phase: str, charged: int) -> int:
    field = _pipeline().phases[phase].loop_policy.iteration_state_field
    state = PipelineState(phase=phase, loop_iterations={field: charged})
    return progress.AnalysisLoopCounter(
        progress.completed_analysis_passes(state, phase, _pipeline()),
        progress.resolve_analysis_cap(field, _pipeline()),
    ).display_iteration


def test_the_first_gated_pass_is_labelled_one() -> None:
    assert _label("development_analysis", charged=1) == 1


def test_each_gated_pass_gets_its_own_label() -> None:
    cap = _pipeline().loop_counters["development_analysis_iteration"].default_max

    labels = [_label("development_analysis", charged=n) for n in range(1, cap + 1)]

    assert labels == list(range(1, cap + 1))


def test_an_ungated_analysis_phase_is_unchanged() -> None:
    assert _label("planning_analysis", charged=0) == 1
    assert _label("planning_analysis", charged=1) == 2


def test_the_final_hint_waits_for_the_last_gated_pass() -> None:
    pipeline = _pipeline()
    cap = pipeline.loop_counters["development_analysis_iteration"].default_max
    field = "development_analysis_iteration"

    def _is_final(charged: int) -> bool:
        state = PipelineState(phase="development_analysis", loop_iterations={field: charged})
        return progress.is_final_analysis_iteration(
            progress.completed_analysis_passes(state, "development_analysis", pipeline), cap
        )

    assert _is_final(cap - 1) is False
    assert _is_final(cap) is True
