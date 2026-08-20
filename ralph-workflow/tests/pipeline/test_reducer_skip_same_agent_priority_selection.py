"""Regression tests for priority selection on skip-same-agent failures."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_retry_intent import AgentRetryIntent
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
    with tempfile.TemporaryDirectory() as directory:
        return load_policy(Path(directory) / ".agent").pipeline


def _state(current_index: int = 1) -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode", "agy"],
                current_index=current_index,
            )
        },
        agent_retry_intent=AgentRetryIntent(skip_same_agent_retries=True),
    )


def _controller(
    entries: dict[str, UnavailabilityEntry] | None = None,
) -> RecoveryController:
    return RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=FakeClock(start=0.0),
            unavailability_entries=entries,
        )
    )


def _cooldown(until_ms: int) -> UnavailabilityEntry:
    return UnavailabilityEntry(
        unavailable_until_ms=until_ms,
        reason=UnavailabilityReason.NO_OUTPUT_AT_START,
        attempt=0,
        base_backoff_ms=until_ms,
        max_backoff_ms=until_ms,
    )


def test_reducer_regression_skip_same_agent_returns_to_highest_priority_available_agent() -> None:
    """S-2: skip path selects index zero instead of advancing from index one."""
    reduced_state, effects = reduce(
        _state(),
        PipelineEvent.AGENT_FAILURE,
        _minimal_policy(),
        recovery=_controller(),
    )

    chain = reduced_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 0
    assert effects == []


def test_reducer_regression_skip_same_agent_never_selects_agent_in_cooldown() -> None:
    """S-2: skip path ignores a higher-priority agent that remains in cooldown."""
    reduced_state, effects = reduce(
        _state(),
        PipelineEvent.AGENT_FAILURE,
        _minimal_policy(),
        recovery=_controller({"development:claude": _cooldown(5000)}),
    )

    chain = reduced_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 2
    assert effects == []


def test_reducer_regression_skip_same_agent_waits_when_every_agent_is_in_cooldown() -> None:
    """S-2: skip path waits instead of failing when no chain agent is selectable."""
    entries = {
        "development:claude": _cooldown(3000),
        "development:opencode": _cooldown(1000),
        "development:agy": _cooldown(5000),
    }

    reduced_state, effects = reduce(
        _state(),
        PipelineEvent.AGENT_FAILURE,
        _minimal_policy(),
        recovery=_controller(entries),
    )

    chain = reduced_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1
    assert reduced_state.is_waiting_state is True
    assert reduced_state.last_retry_delay_ms == 1000
    assert effects == []
