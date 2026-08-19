"""Regression tests for priority agent selection in RecoveryController."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason


def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _no_output_opts() -> InactivityTimeoutOpts:
    return InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_OUTPUT_AT_START,
        diagnostic={"invocation_elapsed": 30.0},
    )


def _three_agent_state(current_index: int = 1) -> PipelineState:
    chain_state = AgentChainState(
        agents=["claude", "opencode", "agy"],
        current_index=current_index,
        retries=0,
    )
    return PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")


def test_return_to_preferred_agent_after_cooldown_expiry() -> None:
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
            policy_bundle=_minimal_policy_bundle(),
            unavailability_entries=initial_entries,
        )
    )
    state = _three_agent_state(current_index=1)
    clock.advance(6.0)

    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    new_state, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="opencode"),
    )
    chain = new_state.chain_for_phase(new_state.phase)
    assert chain is not None
    assert chain.current_index == 0


def test_priority_beats_proximity() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _three_agent_state(current_index=1)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    new_state, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="opencode"),
    )
    chain = new_state.chain_for_phase(new_state.phase)
    assert chain is not None
    assert chain.current_index == 0


def test_priority_beats_same_agent_retry() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _three_agent_state(current_index=1)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    new_state, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="opencode"),
    )
    chain = new_state.chain_for_phase(new_state.phase)
    assert chain is not None
    assert chain.current_index == 0


def test_cooldown_is_never_picked() -> None:
    clock = FakeClock(start=0.0)
    initial_entries = {
        "development:claude": UnavailabilityEntry(
            unavailable_until_ms=10000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=10000,
            max_backoff_ms=10000,
        ),
    }
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            unavailability_entries=initial_entries,
        )
    )
    state = _three_agent_state(current_index=1)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    new_state, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="opencode"),
    )
    chain = new_state.chain_for_phase(new_state.phase)
    assert chain is not None
    assert chain.current_index != 0


def test_all_in_cooldown_still_waits() -> None:
    clock = FakeClock(start=0.0)
    initial_entries = {
        "development:claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
        "development:opencode": UnavailabilityEntry(
            unavailable_until_ms=8000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=8000,
            max_backoff_ms=8000,
        ),
        "development:agy": UnavailabilityEntry(
            unavailable_until_ms=10000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=10000,
            max_backoff_ms=10000,
        ),
    }
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            unavailability_entries=initial_entries,
        )
    )
    state = _three_agent_state(current_index=2)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("agy", 30.0, opts=opts)
    new_state, effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="agy"),
    )
    assert new_state.is_waiting_state is True
    assert new_state.last_retry_delay_ms == 5000
    assert new_state.phase == "development"
    assert effects == []


def test_transcript_visibility() -> None:
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
            policy_bundle=_minimal_policy_bundle(),
            unavailability_entries=initial_entries,
        )
    )
    state = _three_agent_state(current_index=1)
    logs: list[str] = []

    def sink(msg: Any) -> None:
        logs.append(str(msg))

    sink_id = logger.add(sink, level="INFO", format="{message}")
    try:
        opts = _no_output_opts()
        exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
        _new_state, _effects, _evt = controller.handle(
            state,
            exc,
            FailureContext(phase="development", agent="opencode"),
        )
    finally:
        logger.remove(sink_id)

    assert any("Selected agent" in line for line in logs)
    assert any("cooldown (5000ms remaining)" in line for line in logs)


def test_non_unavailable_failures_re_prefer() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _three_agent_state(current_index=1)
    exc = RuntimeError("generic failure")
    new_state, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="opencode"),
    )
    chain = new_state.chain_for_phase(new_state.phase)
    assert chain is not None
    assert chain.current_index == 0
