from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ralph.pipeline.cycle_timing import RoutingTiming
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
_SPENT = 36001.0
_TARGET = "development_final_commit_review"


@lru_cache(maxsize=1)
def _bundle() -> object:
    return load_policy(_DEFAULTS_DIR)


def _developing(consumed: float) -> PipelineState:
    return PipelineState(
        phase="development",
        budget_caps={"iteration": 5},
        phase_chains={"development": AgentChainState(agents=["claude", "codex"])},
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=consumed,
    )


def _fail_with_recovery(state: PipelineState, elapsed: float) -> PipelineState:
    next_state, _effects = reducer_reduce(
        state,
        PhaseFailureEvent(
            phase="development",
            reason="development_result artifact failed validation",
            recoverable=True,
        ),
        _bundle().pipeline,
        recovery=RecoveryController(options=RecoveryControllerOptions(policy_bundle=_bundle())),
        routing_timing=RoutingTiming(total_elapsed_seconds=elapsed),
    )
    return next_state


def test_a_recoverable_failure_in_a_spent_cycle_is_redirected() -> None:
    redirected = _fail_with_recovery(_developing(_SPENT), _SPENT)

    assert redirected.phase == _TARGET
    assert redirected.cycle_timebox_active is True
    assert redirected.cycle_timebox_redirects == 1


def test_a_recoverable_failure_inside_the_budget_still_recovers() -> None:
    recovered = _fail_with_recovery(_developing(1000.0), 1000.0)

    assert recovered.phase == "development"
    assert recovered.cycle_timebox_active is True


def test_repeated_failures_cannot_outlast_the_deadline() -> None:
    state = _developing(_SPENT)

    for _ in range(5):
        state = _fail_with_recovery(state, _SPENT)
        if state.phase != "development":
            break

    assert state.phase == _TARGET


def test_a_crash_in_a_spent_cycle_is_redirected_not_retried() -> None:
    from ralph.pipeline import runner as runner_module

    recovered, _effects = runner_module._reduce_runtime_recovery(
        _developing(_SPENT),
        _bundle().pipeline,
        reason="Pipeline step crashed: RuntimeError: transport died",
        recovery=RecoveryController(options=RecoveryControllerOptions(policy_bundle=_bundle())),
        exc=RuntimeError("transport died"),
        routing_timing=RoutingTiming(total_elapsed_seconds=_SPENT),
    )

    assert recovered.phase == _TARGET
    assert recovered.cycle_timebox_redirects == 1


def test_a_crash_inside_the_budget_still_recovers_normally() -> None:
    from ralph.pipeline import runner as runner_module

    recovered, _effects = runner_module._reduce_runtime_recovery(
        _developing(1000.0),
        _bundle().pipeline,
        reason="Pipeline step crashed: RuntimeError: transport died",
        recovery=RecoveryController(options=RecoveryControllerOptions(policy_bundle=_bundle())),
        exc=RuntimeError("transport died"),
        routing_timing=RoutingTiming(total_elapsed_seconds=1000.0),
    )

    assert recovered.phase == "development"


def test_a_typed_broken_agent_failure_in_a_spent_cycle_is_redirected() -> None:
    from ralph.pipeline.agent_retry_intent import AgentRetryIntent

    spent = _developing(_SPENT).copy_with(
        agent_retry_intent=AgentRetryIntent(
            failure_reason="BrokenAgentExitError",
            failed_agent_name="claude",
            broken_agent_reason="no_output",
        )
    )

    redirected, _effects = reducer_reduce(
        spent,
        PipelineEvent.AGENT_FAILURE,
        _bundle().pipeline,
        recovery=RecoveryController(options=RecoveryControllerOptions(policy_bundle=_bundle())),
        routing_timing=RoutingTiming(total_elapsed_seconds=_SPENT),
    )

    assert redirected.phase == _TARGET
    assert redirected.cycle_timebox_redirects == 1


def test_a_worker_failure_in_a_spent_cycle_is_redirected() -> None:
    from ralph.pipeline.events import WorkerFailedEvent

    redirected, _effects = reducer_reduce(
        _developing(_SPENT),
        WorkerFailedEvent(unit_id="unit-a", exit_code=1, error="worker died"),
        _bundle().pipeline,
        recovery=RecoveryController(options=RecoveryControllerOptions(policy_bundle=_bundle())),
        routing_timing=RoutingTiming(total_elapsed_seconds=_SPENT),
    )

    assert redirected.phase == _TARGET
    assert redirected.cycle_timebox_redirects == 1
