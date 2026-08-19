"""Regression tests for reducer AGENT_FAILURE path priority agent selection."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy


def _minimal_policy() -> PipelinePolicy:
    with tempfile.TemporaryDirectory() as d:
        bundle = load_policy(Path(d) / ".agent")
        return bundle.pipeline


def _three_agent_state(current_index: int = 1, retries: int = 3) -> PipelineState:
    chain_state = AgentChainState(
        agents=["claude", "opencode", "agy"],
        current_index=current_index,
        retries=retries,
    )
    return PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    )


def test_reducer_fallover_returns_to_preferred_available_agent() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
        )
    )
    # Index 1 (opencode), index 0 (claude) is available. Retries at cap (3).
    state = _three_agent_state(current_index=1, retries=3)
    policy = _minimal_policy()

    reduced_state, _effects = reduce(
        state,
        PipelineEvent.AGENT_FAILURE,
        policy,
        recovery=controller,
    )
    chain = reduced_state.chain_for_phase(reduced_state.phase)
    assert chain is not None
    assert chain.current_index == 0
    assert chain.retries == 0
    assert reduced_state.metrics.total_fallbacks == 1


def test_reducer_fallover_skips_agent_in_cooldown() -> None:
    clock = FakeClock(start=0.0)
    initial_entries = {
        "development:claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
    }
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            unavailability_entries=initial_entries,
        )
    )
    # Index 1 (opencode), claude in cooldown (5000ms). Retries at cap (3).
    state = _three_agent_state(current_index=1, retries=3)
    policy = _minimal_policy()

    reduced_state, _effects = reduce(
        state,
        PipelineEvent.AGENT_FAILURE,
        policy,
        recovery=controller,
    )
    chain = reduced_state.chain_for_phase(reduced_state.phase)
    assert chain is not None
    assert chain.current_index == 2


def test_reducer_priority_beats_same_agent_retry() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
        )
    )
    # Index 1 (opencode), claude (index 0) is available, retries below cap (0).
    state = _three_agent_state(current_index=1, retries=0)
    policy = _minimal_policy()

    reduced_state, _effects = reduce(
        state,
        PipelineEvent.AGENT_FAILURE,
        policy,
        recovery=controller,
    )
    chain = reduced_state.chain_for_phase(reduced_state.phase)
    assert chain is not None
    assert chain.current_index == 0


def test_reducer_recovery_none_preserves_forward_only_behaviour() -> None:
    # Index 1 (opencode), retries at cap (3). With recovery=None, uses forward-only (advance to index 2).
    state = _three_agent_state(current_index=1, retries=3)
    policy = _minimal_policy()

    reduced_state, _effects = reduce(
        state,
        PipelineEvent.AGENT_FAILURE,
        policy,
        recovery=None,
    )
    chain = reduced_state.chain_for_phase(reduced_state.phase)
    assert chain is not None
    assert chain.current_index == 2
