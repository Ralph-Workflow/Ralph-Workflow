"""Black-box coverage for the independent development hard-stop timer."""

from __future__ import annotations

from pathlib import Path

from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy, WatchdogVerdict
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline import runner as runner_module
from ralph.pipeline.cycle_timing import (
    RoutingTiming,
    apply_development_timebox,
    cycle_deadline_epochs,
    development_deadline_epochs,
    initialize_legacy_cycle_on_resume,
    initialize_legacy_development_timebox_on_resume,
)
from ralph.pipeline.events import AnalysisDecisionEvent, PhaseFailureEvent, PipelineEvent
from ralph.pipeline.reducer import redirect_expired_cycle_in_place
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import DevelopmentTimeboxPolicy
from ralph.recovery.classifier import FailureCategory
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.timeout_defaults import IDLE_TIMEOUT_SECONDS, MAX_SESSION_SECONDS

_DEFAULTS = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def _policy():
    return load_policy(_DEFAULTS).pipeline


def _timing(development_elapsed: float, cycle_elapsed: float = 0.0) -> RoutingTiming:
    return RoutingTiming(cycle_elapsed, development_elapsed_seconds=development_elapsed)


def test_defaults_start_at_development_and_preserve_pre_warning_retry_elapsed() -> None:
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
        routing_timing=_timing(4199.0),
    )
    assert retry.state.dev_timebox_active
    assert retry.target_phase == "development"


def test_checkpoint_restore_keeps_original_development_start_epoch() -> None:
    from dataclasses import dataclass

    @dataclass
    class ClockDeps:
        monotonic: object
        wall_time: object

    policy = _policy()
    started = apply_development_timebox(
        PipelineState(phase="planning_analysis"),
        "development",
        policy=policy,
        routing_timing=RoutingTiming(0.0, 0.0, current_epoch=1000.0),
    ).state.model_copy(update={"phase": "development"})
    restored = PipelineState.model_validate_json(started.model_dump_json())
    deps = ClockDeps(monotonic=lambda: 10.0, wall_time=lambda: 5200.0)

    sampled, _, timing, _, _, _ = runner_module._sample_step_routing_timing(
        restored,
        policy,
        deps,
        [None],
        [None],
    )

    assert restored.dev_timebox_started_at_epoch == 1000.0
    assert timing is not None
    assert timing.development_elapsed_seconds == 4200.0
    assert sampled.dev_timebox_consumed_seconds == 4200.0


def test_active_legacy_checkpoint_without_epoch_fails_closed_at_warning() -> None:
    from dataclasses import dataclass

    @dataclass
    class ClockDeps:
        monotonic: object
        wall_time: object

    policy = _policy()
    restored = PipelineState.model_validate_json(
        PipelineState(
            phase="development",
            dev_timebox_active=True,
            dev_timebox_consumed_seconds=4199.0,
        ).model_dump_json(exclude={"dev_timebox_started_at_epoch"})
    )
    restored = initialize_legacy_development_timebox_on_resume(restored, policy)
    deps = ClockDeps(monotonic=lambda: 10.0, wall_time=lambda: 5200.0)

    sampled, _, _, timing, _, _ = runner_module._sample_step_routing_timing(
        restored,
        policy,
        deps,
        [None],
        [None],
    )
    redirected = redirect_expired_cycle_in_place(sampled, policy, timing)

    assert sampled.dev_timebox_consumed_seconds == 4200.0
    assert redirected is not None
    assert redirected[0].phase == "development_commit_cleanup"


def test_active_legacy_cycle_without_epoch_fails_closed_at_deadline() -> None:
    from dataclasses import dataclass

    @dataclass
    class ClockDeps:
        monotonic: object
        wall_time: object

    policy = _policy()
    restored = PipelineState.model_validate_json(
        PipelineState(
            phase="development",
            cycle_timebox_active=True,
            cycle_timebox_consumed_seconds=100.0,
        ).model_dump_json(exclude={"cycle_timebox_started_at_epoch"})
    )
    restored = initialize_legacy_cycle_on_resume(restored, policy)
    deps = ClockDeps(monotonic=lambda: 10.0, wall_time=lambda: 5200.0)

    sampled, _, _, timing, _, _ = runner_module._sample_step_routing_timing(
        restored,
        policy,
        deps,
        [None],
        [None],
    )
    redirected = redirect_expired_cycle_in_place(sampled, policy, timing)

    assert sampled.cycle_timebox_consumed_seconds == 36_000.0
    assert redirected is not None
    assert redirected[0].phase == "development_final_commit_cleanup"


def test_checkpoint_file_preserves_both_independent_timer_origins(tmp_path: Path) -> None:
    from ralph.pipeline import checkpoint

    path = tmp_path / "checkpoint.json"
    state = PipelineState(
        phase="development",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=1200.0,
        cycle_timebox_started_at_epoch=1000.0,
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=600.0,
        dev_timebox_started_at_epoch=1600.0,
    )

    checkpoint.save(state, path)
    restored = checkpoint.load(path)

    assert restored is not None
    assert restored.cycle_timebox_started_at_epoch == 1000.0
    assert restored.dev_timebox_started_at_epoch == 1600.0
    assert restored.cycle_timebox_consumed_seconds == 1200.0
    assert restored.dev_timebox_consumed_seconds == 600.0


def test_phase_wide_timer_redirects_on_first_restart_at_warning() -> None:
    policy = _policy()
    clock = FakeClock()
    state = apply_development_timebox(
        PipelineState(phase="planning_analysis"),
        "development",
        policy=policy,
        routing_timing=_timing(0.0),
    ).state.model_copy(
        update={
            "phase": "development",
            "phase_chains": {"development": AgentChainState(agents=["claude"])},
        },
    )

    clock.advance(4200.0)
    warning_timing = _timing(clock.monotonic())
    assert development_deadline_epochs(
        state, "development", policy=policy, routing_timing=warning_timing, now_epoch=1000.0
    ) == (1000.0, 2200.0)
    redirected, _ = reducer_reduce(
        state,
        PhaseFailureEvent(
            phase="development",
            reason="artifact validation failed",
            recoverable=True,
            failure_category=FailureCategory.ARTIFACT_VALIDATION,
        ),
        policy,
        routing_timing=warning_timing,
    )
    assert redirected.phase == "development_commit_cleanup"
    assert redirected.dev_timebox_active is True
    assert redirected.pending_cycle_outcome is None
    assert redirected.post_commit_phase_override == "development_analysis"


def test_same_phase_retry_past_warning_redirects_before_hard_deadline() -> None:
    policy = _policy()
    state = PipelineState(
        phase="development",
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=4199.0,
    )
    assert redirect_expired_cycle_in_place(state, policy, _timing(4199.0)) is None
    redirected = redirect_expired_cycle_in_place(state, policy, _timing(4201.0))
    assert redirected is not None
    next_state, _ = redirected
    assert next_state.phase == "development_commit_cleanup"
    assert next_state.dev_timebox_active is True
    assert next_state.dev_timebox_redirect_reason is not None


def test_same_phase_retry_redirects_at_warning_before_hard_deadline() -> None:
    policy = _policy()
    state = PipelineState(
        phase="development",
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=4199.0,
    )
    assert redirect_expired_cycle_in_place(state, policy, _timing(4199.0)) is None
    redirected = redirect_expired_cycle_in_place(state, policy, _timing(4200.0))
    assert redirected is not None
    next_state, _ = redirected
    assert next_state.phase == "development_commit_cleanup"


def test_development_timebox_regression_deadline_routes_without_cycle_timebox() -> None:
    policy = _policy().model_copy(update={"cycle_timebox": None})
    state = PipelineState(
        phase="development",
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=5400.0,
    )

    sampled_state, _, _, routing_timing, _, _ = runner_module._sample_step_routing_timing(
        state,
        policy,
        None,
        None,
        [0.0],
    )

    assert sampled_state.dev_timebox_consumed_seconds >= 5400.0
    redirected = redirect_expired_cycle_in_place(state, policy, routing_timing)
    assert redirected is not None
    next_state, _ = redirected
    assert next_state.phase == "development_commit_cleanup"


def test_default_watchdog_ceiling_cannot_preempt_development_redirect() -> None:
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=IDLE_TIMEOUT_SECONDS, max_session_seconds=MAX_SESSION_SECONDS
        ),
        clock,
    )

    assert watchdog.evaluate(classify_quiet=lambda: False) is WatchdogVerdict.CONTINUE
    clock.advance(5400.0)
    assert watchdog.evaluate(classify_quiet=lambda: False) is WatchdogVerdict.CONTINUE
    policy = _policy()
    assert policy.development_timebox is not None
    decision = apply_development_timebox(
        PipelineState(phase="development", dev_timebox_active=True),
        "development",
        policy=policy,
        routing_timing=_timing(5400.0),
    )
    assert decision.redirected is True


def test_development_timebox_regression_cycle_only_timing_is_a_noop() -> None:
    """S-1: a missing development clock must never borrow the cycle clock."""
    policy = _policy()
    state = PipelineState(phase="development", dev_timebox_active=True)
    cycle_only_timing = RoutingTiming(total_elapsed_seconds=36_000.0)

    decision = apply_development_timebox(
        state, "development", policy=policy, routing_timing=cycle_only_timing
    )

    assert decision.target_phase == "development"
    assert not decision.redirected
    assert decision.state.dev_timebox_active
    assert (
        development_deadline_epochs(
            state,
            "development",
            policy=policy,
            routing_timing=cycle_only_timing,
            now_epoch=1000.0,
        )
        is None
    )


def test_cycle_expiry_does_not_interrupt_active_development_timebox() -> None:
    policy = _policy()
    state = PipelineState(
        phase="development",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=36_001.0,
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=60.0,
    )
    timing = _timing(60.0, cycle_elapsed=36_001.0)

    assert development_deadline_epochs(
        state, "development", policy=policy, routing_timing=timing, now_epoch=1000.0
    ) == (5140.0, 6340.0)
    assert redirect_expired_cycle_in_place(state, policy, timing) is None
    assert state.dev_timebox_active
    assert state.dev_timebox_consumed_seconds == 60.0
    assert state.cycle_timebox_active
    assert state.cycle_timebox_consumed_seconds == 36_001.0


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
    timing = _timing(600.0, cycle_elapsed=1234.0)
    before = cycle_deadline_epochs(
        state, "development", policy=policy, routing_timing=timing, now_epoch=1000.0
    )
    decision = apply_development_timebox(state, "development", policy=custom, routing_timing=timing)
    after = cycle_deadline_epochs(
        decision.state, "development", policy=custom, routing_timing=timing, now_epoch=1000.0
    )
    assert decision.redirected
    assert decision.state.cycle_timebox_active is True
    assert decision.state.cycle_timebox_consumed_seconds == 1234.0
    assert after == before

    redirected = redirect_expired_cycle_in_place(state, custom, timing)
    assert redirected is not None
    redirected_state, _ = redirected
    assert redirected_state.cycle_timebox_active is True
    assert redirected_state.cycle_timebox_consumed_seconds == 1234.0


def test_development_commit_ends_phase_timer_without_resetting_cycle_timer() -> None:
    policy = _policy()
    state = PipelineState(
        phase="development",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=4200.0,
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=4200.0,
    )
    final_commit, _ = reducer_reduce(
        state,
        PipelineEvent.AGENT_SUCCESS,
        policy,
        routing_timing=_timing(4200.0),
    )
    assert final_commit.phase == "development_commit_cleanup"
    assert final_commit.dev_timebox_active is True
    assert final_commit.dev_timebox_consumed_seconds == 4200.0
    assert final_commit.cycle_timebox_active is True
    assert final_commit.cycle_timebox_consumed_seconds == 4200.0

    committed, _ = reducer_reduce(
        final_commit,
        PipelineEvent.AGENT_SUCCESS,
        policy,
        routing_timing=_timing(4200.0),
    )
    assert committed.phase == "development_commit"
    assert committed.dev_timebox_active is False
    assert committed.cycle_timebox_active is True

    completed, _ = reducer_reduce(
        state,
        PipelineEvent.COMPLETE,
        policy,
        routing_timing=_timing(4200.0),
    )
    assert completed.phase == policy.terminal_phase
    assert completed.dev_timebox_active is True


def test_validation_retry_preserves_elapsed_then_redirects_at_warning() -> None:
    policy = _policy()
    state = PipelineState(
        phase="development",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=1234.0,
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=4199.0,
        phase_chains={"development": AgentChainState(agents=["claude"])},
    )
    warning_timing = _timing(4199.0, cycle_elapsed=1234.0)

    retried, _ = reducer_reduce(
        state,
        PhaseFailureEvent(
            phase="development",
            reason="artifact validation failed",
            recoverable=True,
            failure_category=FailureCategory.ARTIFACT_VALIDATION,
        ),
        policy,
        routing_timing=warning_timing,
    )
    assert retried.phase == "development"
    assert retried.dev_timebox_active
    assert retried.dev_timebox_consumed_seconds == 4199.0
    assert development_deadline_epochs(
        retried, "development", policy=policy, routing_timing=warning_timing, now_epoch=1000.0
    ) == (1001.0, 2201.0)

    redirected, _ = reducer_reduce(
        retried,
        PipelineEvent.AGENT_RETRY,
        policy,
        routing_timing=_timing(4200.0, cycle_elapsed=1234.0),
    )
    assert redirected.phase == "development_commit_cleanup"
    assert redirected.dev_timebox_redirect_reason is not None


def test_agent_failure_fallback_preserves_development_elapsed_time() -> None:
    policy = _policy()
    state = PipelineState(
        phase="development",
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=4100.0,
        phase_chains={"development": AgentChainState(agents=["first", "fallback"], retries=3)},
    )

    fallback, _ = reducer_reduce(
        state,
        PipelineEvent.AGENT_FAILURE,
        policy,
        routing_timing=_timing(4100.0),
    )

    assert fallback.phase == "development"
    assert fallback.dev_timebox_active is True
    assert fallback.dev_timebox_consumed_seconds == 4100.0


def test_recovery_controller_cannot_select_another_agent_after_warning() -> None:
    bundle = load_policy(_DEFAULTS)
    state = PipelineState(
        phase="development",
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=4200.0,
        phase_chains={"development": AgentChainState(agents=["first", "fallback"])},
    )

    redirected, _ = reducer_reduce(
        state,
        PhaseFailureEvent(
            phase="development",
            reason="provider quota exhausted",
            recoverable=True,
        ),
        bundle.pipeline,
        recovery=RecoveryController(options=RecoveryControllerOptions(policy_bundle=bundle)),
        routing_timing=_timing(4200.0),
    )

    assert redirected.phase == "development_commit_cleanup"
    assert redirected.dev_timebox_active is True
    assert redirected.chain_for_phase("development").current_index == 0


def test_warning_routes_through_commit_and_analysis_before_fresh_development() -> None:
    policy = _policy()
    state = PipelineState(
        phase="development",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=4200.0,
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=4200.0,
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        phase_chains={"development": AgentChainState(agents=["first", "fallback"])},
    )

    state, _ = reducer_reduce(
        state,
        PipelineEvent.AGENT_FAILURE,
        policy,
        routing_timing=_timing(4200.0, 4200.0),
    )
    assert state.phase == "development_commit_cleanup"
    assert state.dev_timebox_active is True
    assert state.cycle_timebox_active is True

    state, _ = reducer_reduce(
        state,
        PipelineEvent.AGENT_SUCCESS,
        policy,
        routing_timing=_timing(4200.0, 4200.0),
    )
    assert state.phase == "development_commit"
    assert state.dev_timebox_active is False
    assert state.cycle_timebox_active is True

    state, _ = reducer_reduce(
        state,
        PipelineEvent.COMMIT_SUCCESS,
        policy,
        routing_timing=_timing(4200.0, 4200.0),
    )
    assert state.phase == "development_analysis"
    assert state.cycle_timebox_active is True

    state, _ = reducer_reduce(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="request_changes"),
        policy,
        routing_timing=_timing(0.0, 4200.0),
    )
    assert state.phase == "development"
    assert state.dev_timebox_active is True
    assert state.dev_timebox_consumed_seconds == 0.0
    assert state.cycle_timebox_active is True
    assert state.cycle_timebox_consumed_seconds == 4200.0
