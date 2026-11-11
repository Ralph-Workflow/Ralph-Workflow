from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ralph.pipeline.cycle_timing import RoutingTiming
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
_LIMIT = 36000.0
_SPENT = 36001.0


@lru_cache(maxsize=1)
def _pipeline() -> object:
    return load_policy(_DEFAULTS_DIR).pipeline


def _developing(consumed: float) -> PipelineState:
    return PipelineState(
        phase="development",
        budget_caps={"iteration": 5},
        phase_chains={"development": AgentChainState(agents=["claude", "codex"])},
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=consumed,
    )


def _fail(state: PipelineState, elapsed: float) -> PipelineState:
    next_state, _effects = reducer_reduce(
        state,
        PipelineEvent.AGENT_FAILURE,
        _pipeline(),
        recovery=RecoveryController(options=RecoveryControllerOptions(cycle_cap=10)),
        routing_timing=RoutingTiming(total_elapsed_seconds=elapsed),
    )
    return next_state


def test_a_retry_inside_a_spent_cycle_is_redirected_instead() -> None:
    redirected = _fail(_developing(_SPENT), _SPENT)

    assert redirected.phase == "development_final_commit_review"
    assert redirected.cycle_timebox_active is True
    assert redirected.cycle_timebox_redirect_reason is not None


def test_retries_inside_the_budget_are_untouched() -> None:
    retried = _fail(_developing(1000.0), 1000.0)

    assert retried.phase == "development"
    assert retried.chain_for_phase("development").retries == 1


def test_a_spent_cycle_cannot_fall_over_to_the_next_agent_either() -> None:
    exhausted = _developing(_SPENT)
    chain = exhausted.chain_for_phase("development")
    exhausted = exhausted.with_phase_chain(
        "development", chain.with_retry_increment().with_retry_increment().with_retry_increment()
    )

    redirected = _fail(exhausted, _SPENT)

    assert redirected.phase == "development_final_commit_review"


def test_a_failure_outside_the_guarded_phase_is_unaffected() -> None:
    planning = PipelineState(
        phase="planning",
        phase_chains={"planning": AgentChainState(agents=["claude"])},
        cycle_timebox_active=False,
    )

    retried = _fail(planning, 0.0)

    assert retried.phase == "planning"


def test_a_continuation_inside_a_spent_cycle_is_redirected_too() -> None:
    redirected, _effects = reducer_reduce(
        _developing(_SPENT),
        PipelineEvent.AGENT_RETRY,
        _pipeline(),
        routing_timing=RoutingTiming(total_elapsed_seconds=_SPENT),
    )

    assert redirected.phase == "development_final_commit_review"


def test_a_stale_active_cycle_redirects_the_start_edge_entry() -> None:
    spent = PipelineState(
        phase="planning_analysis",
        budget_caps={"iteration": 5},
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=_SPENT,
    )

    redirected, _effects = reducer_reduce(
        spent,
        PipelineEvent.AGENT_SUCCESS,
        _pipeline(),
        routing_timing=RoutingTiming(total_elapsed_seconds=_SPENT),
    )

    assert redirected.phase == "development_final_commit_review"
    assert redirected.cycle_timebox_redirect_reason is not None
