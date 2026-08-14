"""A spent cycle must not keep launching development invocations under recovery.

The retry guard on the plain failure handler only runs when no recovery
controller is supplied — and in production one always is, so the guard sat on
a path only tests take. Every controller route (`_apply_chain_retry`,
`_handle_retry_progression`) keeps the run in the same phase, crossing no
routing boundary, so the deadline was never consulted: an artifact-validation
failure could restart development ten times per agent after the budget was
gone, leave the cycle armed at a terminal, and report none of it.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ralph.pipeline.cycle_timing import RoutingTiming
from ralph.pipeline.events import PhaseFailureEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
_SPENT = 7300.0
_TARGET = "development_final_commit_cleanup"


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
        recovery=RecoveryController(
            options=RecoveryControllerOptions(policy_bundle=_bundle())
        ),
        routing_timing=RoutingTiming(monotonic_now=0.0, total_elapsed_seconds=elapsed),
    )
    return next_state


def test_a_recoverable_failure_in_a_spent_cycle_is_redirected() -> None:
    """The controller's retry would be another full-length invocation."""
    redirected = _fail_with_recovery(_developing(_SPENT), _SPENT)

    assert redirected.phase == _TARGET
    assert redirected.cycle_timebox_active is False
    assert redirected.cycle_timebox_redirects == 1


def test_a_recoverable_failure_inside_the_budget_still_recovers() -> None:
    """With budget left the controller handles the failure as it always did."""
    recovered = _fail_with_recovery(_developing(1000.0), 1000.0)

    assert recovered.phase == "development"
    assert recovered.cycle_timebox_active is True


def test_repeated_failures_cannot_outlast_the_deadline() -> None:
    """The premise: a persistently failing agent cannot mine the spent budget."""
    state = _developing(_SPENT)

    for _ in range(5):
        state = _fail_with_recovery(state, _SPENT)
        if state.phase != "development":
            break

    assert state.phase == _TARGET
