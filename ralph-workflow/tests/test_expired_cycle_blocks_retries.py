"""A spent cycle must not keep starting fresh development invocations.

Retry and chain-fallover leave the run in the same phase, so no routing
boundary is crossed and the deadline — enforced only at boundaries — is never
consulted. With three retries per agent times the length of the agent chain,
an expired cycle could still run a double-digit number of full-length
development invocations, while the prompt, the tool-result banner and the
policy documentation all state that the redirect happens instead of starting
another development invocation.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ralph.pipeline.cycle_timing import RoutingTiming
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
_LIMIT = 7200.0
_SPENT = 7300.0


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
        routing_timing=RoutingTiming(total_elapsed_seconds=elapsed),
    )
    return next_state


def test_a_retry_inside_a_spent_cycle_is_redirected_instead() -> None:
    """The retry would be a fresh full-length invocation on a spent budget."""
    redirected = _fail(_developing(_SPENT), _SPENT)

    assert redirected.phase == "development_final_commit_cleanup"
    assert redirected.cycle_timebox_active is False
    assert redirected.cycle_timebox_redirect_reason is not None


def test_retries_inside_the_budget_are_untouched() -> None:
    """A failure with budget left still retries, as it always did."""
    retried = _fail(_developing(1000.0), 1000.0)

    assert retried.phase == "development"
    assert retried.chain_for_phase("development").retries == 1


def test_a_spent_cycle_cannot_fall_over_to_the_next_agent_either() -> None:
    """Chain fallover is the same unbudgeted invocation by another name."""
    exhausted = _developing(_SPENT)
    chain = exhausted.chain_for_phase("development")
    exhausted = exhausted.with_phase_chain(
        "development", chain.with_retry_increment().with_retry_increment().with_retry_increment()
    )

    redirected = _fail(exhausted, _SPENT)

    assert redirected.phase == "development_final_commit_cleanup"


def test_a_failure_outside_the_guarded_phase_is_unaffected() -> None:
    """Only the deadline-guarded phase is bounded by the cycle budget."""
    planning = PipelineState(
        phase="planning",
        phase_chains={"planning": AgentChainState(agents=["claude"])},
        cycle_timebox_active=False,
    )

    retried = _fail(planning, 0.0)

    assert retried.phase == "planning"


def test_a_continuation_inside_a_spent_cycle_is_redirected_too() -> None:
    """A continuation is another full-length invocation of the same phase."""
    redirected, _effects = reducer_reduce(
        _developing(_SPENT),
        PipelineEvent.AGENT_RETRY,
        _pipeline(),
        routing_timing=RoutingTiming(total_elapsed_seconds=_SPENT),
    )

    assert redirected.phase == "development_final_commit_cleanup"


def test_a_stale_active_cycle_redirects_the_start_edge_entry() -> None:
    """Entering on the start edge with a cycle still armed and spent.

    The start branch only fires when no cycle is running, so an entry made
    while one is still armed falls through to the guarded-entry check and is
    redirected rather than silently starting a second cycle on a spent clock.

    What this does NOT pin: the signal resolver's forwarding of the routing
    timing. Under the bundled graph every edge into the guarded phase is
    reached through the analysis-success or loopback handlers, not the signal
    resolver, so no bundled-policy test can distinguish that forwarding —
    verified by mutating it and watching this stay green.
    """
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

    assert redirected.phase == "development_final_commit_cleanup"
    assert redirected.cycle_timebox_redirect_reason is not None
