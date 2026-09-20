"""Black-box coverage for the independent development hard-stop timer."""

from __future__ import annotations

from pathlib import Path

from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy, WatchdogVerdict
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.cycle_timing import (
    RoutingTiming,
    apply_development_timebox,
    cycle_deadline_epochs,
    development_deadline_epochs,
)
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.pipeline.reducer import redirect_expired_cycle_in_place
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import DevelopmentTimeboxPolicy
from ralph.recovery.classifier import FailureCategory
from ralph.timeout_defaults import IDLE_TIMEOUT_SECONDS, MAX_SESSION_SECONDS

_DEFAULTS = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def _policy():
    return load_policy(_DEFAULTS).pipeline


def _timing(development_elapsed: float, cycle_elapsed: float = 0.0) -> RoutingTiming:
    return RoutingTiming(cycle_elapsed, development_elapsed_seconds=development_elapsed)


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


def test_phase_wide_timer_warns_then_redirects_across_validation_retries() -> None:
    policy = _policy()
    clock = FakeClock()
    state = apply_development_timebox(
        PipelineState(phase="planning_analysis"),
        "development",
        policy=policy,
        routing_timing=_timing(0.0),
    ).state.model_copy(
        update={"phase": "development", "phase_chains": {"development": AgentChainState(agents=["claude"])}},
    )

    clock.advance(4200.0)
    warning_timing = _timing(clock.monotonic())
    assert development_deadline_epochs(
        state, "development", policy=policy, routing_timing=warning_timing, now_epoch=1000.0
    ) == (1000.0, 2200.0)
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

    clock.advance(1200.0)
    redirected = redirect_expired_cycle_in_place(retried, policy, _timing(clock.monotonic()))
    assert redirected is not None
    next_state, _ = redirected
    assert next_state.phase == "development_final_commit_cleanup"
    assert next_state.dev_timebox_active is False


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


def test_default_watchdog_ceiling_cannot_preempt_development_redirect() -> None:
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(idle_timeout_seconds=IDLE_TIMEOUT_SECONDS, max_session_seconds=MAX_SESSION_SECONDS),
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
    assert decision.state.cycle_timebox_consumed_seconds == 1234.0
    assert after == before


def test_leaving_development_resets_timer_only_for_a_later_entry() -> None:
    policy = _policy()
    state = PipelineState(
        phase="development", dev_timebox_active=True, dev_timebox_consumed_seconds=4200.0
    )
    final_commit, _ = reducer_reduce(
        state,
        PipelineEvent.AGENT_SUCCESS,
        policy,
        routing_timing=_timing(4200.0),
    )
    assert final_commit.phase == "development_commit_cleanup"
    assert final_commit.dev_timebox_active is False
    assert final_commit.dev_timebox_consumed_seconds == 4200.0

    completed, _ = reducer_reduce(
        state,
        PipelineEvent.COMPLETE,
        policy,
        routing_timing=_timing(4200.0),
    )
    assert completed.phase == policy.terminal_phase
    assert completed.dev_timebox_active is False

    restarted = apply_development_timebox(
        completed.model_copy(update={"phase": "planning_analysis"}),
        "development",
        policy=policy,
        routing_timing=_timing(0.0),
    ).state
    assert restarted.dev_timebox_active
    assert restarted.dev_timebox_consumed_seconds == 0.0


def test_validation_retry_preserves_original_development_timebox_deadlines() -> None:
    policy = _policy()
    state = PipelineState(
        phase="development",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=1234.0,
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=4200.0,
        phase_chains={"development": AgentChainState(agents=["claude"])},
    )
    warning_timing = _timing(4200.0, cycle_elapsed=1234.0)

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
    assert retried.dev_timebox_consumed_seconds == 4200.0
    assert development_deadline_epochs(
        retried, "development", policy=policy, routing_timing=warning_timing, now_epoch=1000.0
    ) == (1000.0, 2200.0)

    redirected, _ = reducer_reduce(
        retried,
        PipelineEvent.AGENT_RETRY,
        policy,
        routing_timing=_timing(5400.0, cycle_elapsed=1234.0),
    )
    assert redirected.phase == "development_final_commit_cleanup"
    assert redirected.dev_timebox_redirect_reason is not None
