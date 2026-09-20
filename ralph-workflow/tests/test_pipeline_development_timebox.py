"""Black-box coverage for the independent development hard-stop timer."""

from __future__ import annotations

from pathlib import Path

from ralph.pipeline.cycle_timing import RoutingTiming, apply_development_timebox
from ralph.pipeline.reducer import redirect_expired_cycle_in_place
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import DevelopmentTimeboxPolicy

_DEFAULTS = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def _policy():
    return load_policy(_DEFAULTS).pipeline


def _timing(development_elapsed: float) -> RoutingTiming:
    return RoutingTiming(0.0, development_elapsed_seconds=development_elapsed)


def test_defaults_start_at_development_and_preserve_retry_elapsed() -> None:
    policy = _policy()
    assert policy.development_timebox is not None
    assert policy.development_timebox.duration_seconds == 5400.0
    assert policy.development_timebox.warning_seconds == 4200.0
    started = apply_development_timebox(
        PipelineState(phase="planning_analysis"),
        "development",
        policy=policy,
        routing_timing=_timing(0.0),
    ).state
    assert started.dev_timebox_active
    retry = apply_development_timebox(
        started.model_copy(update={"phase": "development"}),
        "development",
        policy=policy,
        routing_timing=_timing(4200.0),
    )
    assert retry.state.dev_timebox_active
    assert retry.target_phase == "development"


def test_same_phase_retry_redirects_at_development_deadline() -> None:
    policy = _policy()
    state = PipelineState(
        phase="development",
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=5399.0,
    )
    assert redirect_expired_cycle_in_place(state, policy, _timing(5399.0)) is None
    redirected = redirect_expired_cycle_in_place(state, policy, _timing(5400.0))
    assert redirected is not None
    next_state, _ = redirected
    assert next_state.phase == "development_final_commit_cleanup"
    assert next_state.dev_timebox_active is False
    assert next_state.dev_timebox_redirect_reason is not None


def test_custom_development_limit_does_not_change_cycle_elapsed() -> None:
    policy = _policy()
    assert policy.development_timebox is not None
    custom = policy.model_copy(
        update={
            "development_timebox": DevelopmentTimeboxPolicy(
                **{
                    **policy.development_timebox.model_dump(),
                    "duration_seconds": 600.0,
                    "warning_seconds": 500.0,
                }
            )
        }
    )
    state = PipelineState(
        phase="development",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=1234.0,
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=599.0,
    )
    decision = apply_development_timebox(
        state, "development", policy=custom, routing_timing=_timing(600.0)
    )
    assert decision.redirected
    assert decision.state.cycle_timebox_consumed_seconds == 1234.0
