from __future__ import annotations

from pathlib import Path

from ralph.pipeline.run_loop import resolve_initial_state
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
_RESUMED_CAP = 5
_REQUESTED_CAP = 12


def _resumed(**overrides: object) -> PipelineState:
    overrides.setdefault("phase", "development")
    overrides.setdefault("budget_caps", {"iteration": _RESUMED_CAP})
    overrides.setdefault("outer_progress", {"iteration": _RESUMED_CAP})
    return PipelineState(**overrides)


def test_resume_adopts_an_explicit_counter_override() -> None:
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
    assert state.outer_progress == resumed.outer_progress
    assert state.phase == resumed.phase


def test_resume_override_leaves_untargeted_counters_alone() -> None:
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


def test_developer_iters_flag_raises_a_resumed_runs_budget() -> None:
    from ralph.cli.main import counter_overrides_with_developer_iters

    bundle = load_policy(_DEFAULTS_DIR)
    overrides = counter_overrides_with_developer_iters({}, _REQUESTED_CAP)

    state = resolve_initial_state(
        config=None,
        policy_bundle=bundle,
        initial_state=_resumed(),
        counter_overrides=overrides,
        pipeline_deps=None,
        pro_hooks=None,
        state_factory=None,
    )

    assert state.budget_caps["iteration"] == _REQUESTED_CAP
    assert state.get_budget_remaining("iteration") == _REQUESTED_CAP - _RESUMED_CAP


def test_resume_initializes_cycle_timing_for_a_legacy_checkpoint() -> None:
    bundle = load_policy(_DEFAULTS_DIR)
    legacy = PipelineState(phase="development", budget_caps={"iteration": _RESUMED_CAP})
    assert legacy.cycle_timebox_active is False

    state = resolve_initial_state(
        config=None,
        policy_bundle=bundle,
        initial_state=legacy,
        counter_overrides=None,
        pipeline_deps=None,
        pro_hooks=None,
        state_factory=None,
    )

    assert state.cycle_timebox_active is True
    assert state.cycle_timebox_consumed_seconds == 0.0


def test_resume_does_not_arm_a_timer_before_the_cycle_starts() -> None:
    bundle = load_policy(_DEFAULTS_DIR)

    state = resolve_initial_state(
        config=None,
        policy_bundle=bundle,
        initial_state=PipelineState(phase="planning"),
        counter_overrides=None,
        pipeline_deps=None,
        pro_hooks=None,
        state_factory=None,
    )

    assert state.cycle_timebox_active is False
