"""AGENT_FAILURE keeps a pending conflict on its current strategy rung."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline import run_loop
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason


def _policy() -> object:
    with tempfile.TemporaryDirectory() as directory:
        return load_policy(Path(directory) / ".agent").pipeline


def _entry(unavailable_until_ms: int) -> UnavailabilityEntry:
    return UnavailabilityEntry(
        unavailable_until_ms=unavailable_until_ms,
        reason=UnavailabilityReason.NO_OUTPUT_AT_START,
        attempt=0,
        base_backoff_ms=5000,
        max_backoff_ms=5000,
    )


def _pending_conflict_state() -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode"], current_index=0, retries=3
            )
        },
    ).copy_with(
        rebase=run_loop.RebaseState(
            last_action="conflict",
            consecutive_conflicts=4,
            conflict_strategy_index=2,
            conflict_strategies_tried=("rebase_resolver: conflict", "refresh_retry: conflict"),
        ),
        is_waiting_state=True,
    )


def _reduce_with(entries: dict[str, UnavailabilityEntry]) -> PipelineState:
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=FakeClock(start=0.0),
            unavailability_entries=entries,
        )
    )
    state, _effects = reduce(
        _pending_conflict_state(), PipelineEvent.AGENT_FAILURE, _policy(), recovery=controller
    )
    return state


def test_agent_failure_falls_over_without_spending_pending_conflict_budget() -> None:
    state = _reduce_with({"claude": _entry(5000)})

    chain = state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1
    assert chain.retries == 0
    assert state.metrics.total_fallbacks == 1
    assert state.rebase == _pending_conflict_state().rebase


def test_agent_failure_waits_when_all_resolvers_are_cooling_down() -> None:
    state = _reduce_with({"claude": _entry(5000), "opencode": _entry(8000)})

    assert state.is_waiting_state is True
    assert state.last_retry_delay_ms == 5000
    assert state.last_error == "all agents unavailable; waiting for cooldown expiry"
    assert state.rebase == _pending_conflict_state().rebase
