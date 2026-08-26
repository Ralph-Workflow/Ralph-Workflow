"""Regression test for priority reselection after cooldown waiting."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline import run_loop
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.integration_resolution import (
    RECOVERABLE,
    RESOLVED,
    IntegrationResolutionVerdict,
)
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

    logs: list[str] = []

    def sink(msg: Any) -> None:
        logs.append(str(msg))

    sink_id = logger.add(sink, level="INFO", format="{message}")
    try:
        run_loop._run_inner_loop(state, ctx, prev_phase="development")
    finally:
        logger.remove(sink_id)

    assert len(seen_states) == 2
    resumed_chain = seen_states[1].chain_for_phase("development")
    assert resumed_chain is not None
    assert resumed_chain.current_index == 0
    assert resumed_chain.retries == 0
    assert seen_states[1].last_agent_session_id is None
    assert seen_states[1].is_waiting_state is False
    assert any("Phase development: Selected agent claude" in log for log in logs)


def test_cooldown_resume_does_not_reselect_when_integration_is_unresolved(
    monkeypatch: Any,
) -> None:
    """A cooldown cannot prepare an ordinary agent while resolution is pending."""
    ctx = MagicMock()
    ctx.workspace_scope.root = Path("/workspace")
    ctx.active_display = MagicMock()
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(agents=["claude", "opencode"], current_index=1)
        },
    ).copy_with(rebase=run_loop.RebaseState(last_action="conflict"), is_waiting_state=True)
    reselections: list[PipelineState] = []
    monkeypatch.setattr(run_loop, "emit_activity_line", lambda *_args: None)
    monkeypatch.setattr(run_loop, "_log_resumed_state", lambda *_args: None)
    monkeypatch.setattr(
        run_loop,
        "_reselect_preferred_agent",
        lambda candidate, _ctx: reselections.append(candidate) or candidate,
    )
    monkeypatch.setattr(
        run_loop,
        "inspect_integration_resolution",
        lambda *_args: IntegrationResolutionVerdict(RECOVERABLE),
    )

    resumed = run_loop._resume_after_cooldown_wait(state, ctx, "development", "offline", 10)

    assert reselections == [], "blocked cooldown resume must not prepare an ordinary dispatch"
    chain = resumed.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1
    assert resumed.is_waiting_state is False


def test_recoverable_mid_run_verdict_reenters_resolution_before_dispatch(
    monkeypatch: Any,
) -> None:
    """A late conflict uses the resolver seam instead of exiting an ordinary phase."""
    config = MagicMock()
    config.general.auto_integrate_enabled = True
    ctx = MagicMock()
    ctx.config = config
    ctx.workspace_scope.root = Path("/workspace")
    state = PipelineState(phase="planning")
    recoverable = IntegrationResolutionVerdict(
        status=RECOVERABLE,
        reasons=("working tree is not clean",),
        recovery_executor="rebase_conflict_resolution",
    )
    resolved = IntegrationResolutionVerdict(
        status=RESOLVED,
    )
    inspections = iter((recoverable, resolved))
    startup = MagicMock(return_value=state.rebase)
    monkeypatch.setattr(run_loop, "inspect_integration_resolution", lambda *_args: next(inspections))
    monkeypatch.setattr(run_loop, "_run_startup_integration", startup)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", lambda *_args: None)

    assert run_loop._block_unresolved_integration(state, ctx, "analysis") is None
    startup.assert_called_once_with(ctx, state.rebase)


def test_exhausted_mid_run_verdict_terminates_without_resolution_reentry(
    monkeypatch: Any,
) -> None:
    """Only durable resolver exhaustion may stop an otherwise ordinary loop."""
    config = MagicMock()
    config.general.auto_integrate_enabled = True
    ctx = MagicMock()
    ctx.config = config
    ctx.workspace_scope.root = Path("/workspace")
    state = PipelineState(phase="planning")
    exhausted = IntegrationResolutionVerdict(
        status=run_loop.EXHAUSTED,
        reasons=("chain exhausted",),
    )
    startup = MagicMock()
    monkeypatch.setattr(run_loop, "inspect_integration_resolution", lambda *_args: exhausted)
    monkeypatch.setattr(run_loop, "_run_startup_integration", startup)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", lambda *_args: None)
    monkeypatch.setattr(run_loop, "_announce_deferred_startup_integration", lambda *_args: None)

    assert run_loop._block_unresolved_integration(state, ctx, "analysis") == (state, "analysis", 1)
    startup.assert_not_called()
