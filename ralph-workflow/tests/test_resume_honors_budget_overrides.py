"""Budget overrides supplied on a resumed run.

A resumed run adopts the checkpoint's state verbatim, which silently discarded
every budget instruction given on the command line: the run kept the caps it
was started with, reported its budget as spent, and ended right after the dev
cycle's final commit even though the operator had just asked for more cycles.
Explicit overrides are the operator's latest instruction and must win over the
caps frozen into the checkpoint.
"""

from __future__ import annotations

from pathlib import Path

from ralph.pipeline.run_loop import resolve_initial_state
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
_RESUMED_CAP = 5
_REQUESTED_CAP = 12


def _resumed(**overrides: object) -> PipelineState:
    return PipelineState(
        phase="development",
        budget_caps={"iteration": _RESUMED_CAP},
        outer_progress={"iteration": _RESUMED_CAP},
        **overrides,
    )


def test_resume_adopts_an_explicit_counter_override() -> None:
    """`--counter iteration=N` on a resumed run raises the resumed cap."""
    bundle = load_policy(_DEFAULTS_DIR)

    state = resolve_initial_state(
        config=None,
        policy_bundle=bundle,
        initial_state=_resumed(),
        counter_overrides={"iteration": _REQUESTED_CAP},
        pipeline_deps=None,
        pro_hooks=None,
        state_factory=None,
    )

    assert state.budget_caps["iteration"] == _REQUESTED_CAP
    assert state.get_budget_remaining("iteration") == _REQUESTED_CAP - _RESUMED_CAP


def test_resume_without_overrides_keeps_the_checkpoint_budget() -> None:
    """No instruction means no change — the checkpoint stays authoritative."""
    bundle = load_policy(_DEFAULTS_DIR)
    resumed = _resumed()

    state = resolve_initial_state(
        config=None,
        policy_bundle=bundle,
        initial_state=resumed,
        counter_overrides=None,
        pipeline_deps=None,
        pro_hooks=None,
        state_factory=None,
    )

    assert state.budget_caps["iteration"] == _RESUMED_CAP
    assert state == resumed


def test_resume_override_leaves_untargeted_counters_alone() -> None:
    """Only the named counter moves; the rest of the checkpoint is preserved."""
    bundle = load_policy(_DEFAULTS_DIR)
    resumed = _resumed(loop_iterations={"development_analysis_iteration": 3})

    state = resolve_initial_state(
        config=None,
        policy_bundle=bundle,
        initial_state=resumed,
        counter_overrides={"iteration": _REQUESTED_CAP},
        pipeline_deps=None,
        pro_hooks=None,
        state_factory=None,
    )

    assert state.get_loop_iteration("development_analysis_iteration") == 3
    assert state.phase == "development"
