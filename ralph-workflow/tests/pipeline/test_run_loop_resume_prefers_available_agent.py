"""Regression test for priority reselection after cooldown waiting."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline import run_loop
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason


def _policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as directory:
        return load_policy(Path(directory) / ".agent")


def _entry(until_ms: int) -> UnavailabilityEntry:
    return UnavailabilityEntry(
        unavailable_until_ms=until_ms,
        reason=UnavailabilityReason.NO_OUTPUT_AT_START,
        attempt=0,
        base_backoff_ms=5000,
        max_backoff_ms=5000,
    )


def test_run_loop_resumes_on_highest_priority_newly_available_agent(
    monkeypatch: Any,
) -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            unavailability_entries={
                "development:claude": _entry(5000),
                "development:opencode": _entry(8000),
                "development:agy": _entry(10000),
            },
        )
    )
    policy_bundle = MagicMock()
    policy_bundle.pipeline.terminal_phase = "complete"
    connectivity_monitor = MagicMock()
    connectivity_monitor.current_state = "online"
    ctx = run_loop._LoopContext(
        policy_bundle=policy_bundle,
        workspace_scope=MagicMock(),
        config=MagicMock(),
        active_display=MagicMock(),
        display_context=MagicMock(),
        effective_verbosity=0,
        registry=MagicMock(),
        effective_pipeline_subscriber=None,
        controller=controller,
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=connectivity_monitor,
        sleep=clock.advance,
        is_quiet=False,
        snapshot_registry=None,
        last_waiting_state_phase=None,
    )
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode", "agy"],
                current_index=2,
                retries=2,
            )
        },
    ).copy_with(last_retry_delay_ms=5000, is_waiting_state=True)

    seen_states: list[PipelineState] = []

    def run_step(*, state: PipelineState, **_kwargs: object) -> PipelineState:
        seen_states.append(state)
        if len(seen_states) == 1:
            return state
        return state.copy_with(phase="complete")

    emitted: list[str] = []
    monkeypatch.setattr("ralph.pipeline.runner.run_pipeline_step", run_step)
    monkeypatch.setattr(
        "ralph.pipeline.run_loop.emit_activity_line",
        lambda _display, _phase, text: emitted.append(text),
    )

    run_loop._run_inner_loop(state, ctx, prev_phase="development")

    assert len(seen_states) == 2
    resumed_chain = seen_states[1].chain_for_phase("development")
    assert resumed_chain is not None
    assert resumed_chain.current_index == 0
    assert resumed_chain.retries == 0
    assert seen_states[1].last_agent_session_id is None
    assert seen_states[1].is_waiting_state is False
    assert any("Selected agent claude" in message for message in emitted) is False
