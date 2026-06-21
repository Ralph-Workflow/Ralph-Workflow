"""Tests for fast no-progress quiet agent detection and recovery flow."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from loguru import logger

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.run_loop import (
    _LoopContext,
    _run_inner_loop,
    _subscribe_recovery_logger,
)
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEventBus, FalloverEvent
from ralph.recovery.failure_classifier import FailureClassifier
from ralph.recovery.unavailability_reason import ReasonBackoffPolicy, UnavailabilityReason

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _make_state(agents: list[str]) -> PipelineState:
    chain_state = AgentChainState(agents=agents, current_index=0, retries=0)
    return PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    )


def test_classifier_flags_structured_unavailable_without_text_match() -> None:
    """FailureClassifier flags is_unavailable based on the structured error reason alone.

    The structured watchdog_reason (NO_PROGRESS_QUIET) short-circuits the OR chain in
    is_unavailable before text matching is evaluated. Even though the
    AgentInactivityTimeoutError message for NO_PROGRESS_QUIET contains
    'produced no output' (which is in UNAVAILABLE_AGENT_SUBSTRINGS), the
    structured reason is checked FIRST in the OR chain, so text matching is
    never evaluated. This proves the structured reason is the deciding signal.
    """
    classifier = FailureClassifier()
    opts = InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_PROGRESS_QUIET,
        diagnostic={"cumulative": 0.0},
    )
    exc = AgentInactivityTimeoutError("claude", 15.0, opts=opts)

    failure = classifier.classify(
        exc,
        phase="development",
        agent="claude",
        connectivity_state="online",
    )

    assert failure.is_unavailable is True
    assert failure.watchdog_reason == "no_progress_quiet"
    assert failure.category.value == "agent"


def test_recovery_controller_falls_over_on_no_progress_quiet() -> None:
    """RecoveryController transitions agent on NO_PROGRESS_QUIET and publishes correct events."""
    clock = FakeClock(start=0.0)
    bus = FailureEventBus()
    events: list[object] = []
    bus.subscribe(events.append)

    policy = {
        UnavailabilityReason.STALE_CHILD_QUIET: ReasonBackoffPolicy(
            base_backoff_ms=5_000, max_backoff_ms=300_000
        )
    }
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=bus,
            unavailability_backoff_policy=policy,
        )
    )
    state = _make_state(["claude", "opencode"]).copy_with(last_connectivity_state="online")

    opts = InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_PROGRESS_QUIET,
        diagnostic={"cumulative": 0.0},
    )
    exc = AgentInactivityTimeoutError("claude", 15.0, opts=opts)

    new_state, _effects, _failure_evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    # Agent transitions to opencode
    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1

    # FalloverEvent is published carrying watchdog_reason
    fallovers = [e for e in events if isinstance(e, FalloverEvent)]
    assert len(fallovers) == 1
    assert fallovers[0].watchdog_reason == "no_progress_quiet"

    # Cooldown timeout is set
    snap = controller.snapshot()
    assert snap["unavailable_timeouts"]["development:claude"] == 5000


def test_run_loop_emits_waiting_then_resumed(monkeypatch: MonkeyPatch) -> None:
    """Run loop emits WAITING status before cooldown sleep and RESUMED after, using the guard."""
    policy_bundle = MagicMock()
    policy_bundle.pipeline.terminal_phase = "complete"
    connectivity_monitor = MagicMock()
    connectivity_monitor.current_state = "online"

    ctx = _LoopContext(
        policy_bundle=policy_bundle,
        workspace_scope=MagicMock(),
        config=MagicMock(),
        active_display=MagicMock(),
        display_context=MagicMock(),
        effective_verbosity=0,
        registry=MagicMock(),
        effective_pipeline_subscriber=None,
        controller=MagicMock(),
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=connectivity_monitor,
        sleep=MagicMock(),
        is_quiet=False,
        snapshot_registry=None,
        last_waiting_state_phase=None,
    )

    emitted: list[str] = []

    def mock_emit_activity_line(display: object, phase: str | None, text: str) -> None:
        emitted.append(text)

    monkeypatch.setattr("ralph.pipeline.run_loop.emit_activity_line", mock_emit_activity_line)

    slept: list[float] = []
    ctx.sleep = slept.append
    ctx.active_display = MagicMock()
    ctx.display_context = MagicMock()
    ctx.effective_verbosity = 0
    ctx.registry = MagicMock()
    ctx.effective_pipeline_subscriber = None
    ctx.controller = MagicMock()
    ctx.config_path = None
    ctx.cli_overrides = {}
    ctx.monitor_stop = None
    ctx.pipeline_deps = None

    chain_state = AgentChainState(agents=["claude"], current_index=0, retries=0)
    state = PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    )
    # Use the structured ``is_waiting_state`` flag as the wait-state signal;
    # the previous ``last_error`` text parser was brittle and was replaced
    # with this boolean. The ``last_error`` text remains as operator
    # context only and is NOT a contract the run loop parses.
    state = state.copy_with(
        last_error=(
            "all agents unavailable (last reason: out_of_credits); waiting for cooldown expiry"
        ),
        last_retry_delay_ms=200,
        is_waiting_state=True,
    )
    # The MagicMock controller is consumed by the run loop through the
    # public ``waiting_state_payload`` and ``agents_now_available`` methods.
    ctx.controller.waiting_state_payload.return_value = [("claude", 1, 200)]
    ctx.controller.agents_now_available.return_value = ["claude"]

    def mock_run_pipeline_step(**_kwargs: object) -> PipelineState:
        return state.copy_with(phase="complete")

    monkeypatch.setattr("ralph.pipeline.runner.run_pipeline_step", mock_run_pipeline_step)

    _run_inner_loop(state, ctx, prev_phase="development")

    assert len(slept) == 1
    assert slept[0] == 0.2

    # Check WAITING and RESUMED emissions
    assert any("WAITING" in str(s) for s in emitted)
    assert any("RESUMED" in str(s) for s in emitted)


def test_run_loop_never_crashes_on_sleep_exception(monkeypatch: MonkeyPatch) -> None:
    """Run loop catches sleep exception, logs it, and continues rather than crashing."""
    policy_bundle = MagicMock()
    policy_bundle.pipeline.terminal_phase = "complete"
    connectivity_monitor = MagicMock()
    connectivity_monitor.current_state = "online"

    ctx = _LoopContext(
        policy_bundle=policy_bundle,
        workspace_scope=MagicMock(),
        config=MagicMock(),
        active_display=MagicMock(),
        display_context=MagicMock(),
        effective_verbosity=0,
        registry=MagicMock(),
        effective_pipeline_subscriber=None,
        controller=MagicMock(),
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=connectivity_monitor,
        sleep=MagicMock(),
        is_quiet=False,
        snapshot_registry=None,
        last_waiting_state_phase=None,
    )

    emitted: list[str] = []

    def mock_emit_activity_line(display: object, phase: str | None, text: str) -> None:
        emitted.append(text)

    monkeypatch.setattr("ralph.pipeline.run_loop.emit_activity_line", mock_emit_activity_line)

    def raising_sleep(seconds: float) -> None:
        raise RuntimeError("simulated sleep crash")

    ctx.sleep = raising_sleep

    ctx.active_display = MagicMock()
    ctx.display_context = MagicMock()
    ctx.effective_verbosity = 0
    ctx.registry = MagicMock()
    ctx.effective_pipeline_subscriber = None
    ctx.controller = MagicMock()
    ctx.config_path = None
    ctx.cli_overrides = {}
    ctx.monitor_stop = None
    ctx.pipeline_deps = None

    chain_state = AgentChainState(agents=["claude"], current_index=0, retries=0)
    state = PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    )
    # Use the structured ``is_waiting_state`` flag as the wait-state signal;
    # the previous ``last_error`` text parser was brittle and was replaced
    # with this boolean. The ``last_error`` text remains as operator
    # context only and is NOT a contract the run loop parses.
    state = state.copy_with(
        last_error=(
            "all agents unavailable (last reason: out_of_credits); waiting for cooldown expiry"
        ),
        last_retry_delay_ms=200,
        is_waiting_state=True,
    )
    # The MagicMock controller is consumed by the run loop through the
    # public ``waiting_state_payload`` and ``agents_now_available`` methods.
    ctx.controller.waiting_state_payload.return_value = [("claude", 1, 200)]
    ctx.controller.agents_now_available.return_value = ["claude"]

    def mock_run_pipeline_step(**_kwargs: object) -> PipelineState:
        return state.copy_with(phase="complete")

    monkeypatch.setattr("ralph.pipeline.runner.run_pipeline_step", mock_run_pipeline_step)

    new_state, _prev_phase, _exit_code = _run_inner_loop(state, ctx, prev_phase="development")

    assert new_state.last_retry_delay_ms == 1000
    assert any("WAITING" in str(s) for s in emitted)


def test_fallover_log_line_includes_watchdog_reason(monkeypatch: MonkeyPatch) -> None:
    """FALLOVER log message includes the watchdog reason."""
    logs: list[str] = []
    sink_id = logger.add(lambda msg: logs.append(msg.record["message"]), level="INFO")

    try:
        clock = FakeClock(start=0.0)
        bus = FailureEventBus()

        controller = RecoveryController(
            options=RecoveryControllerOptions(
                cycle_cap=10,
                clock=clock,
                policy_bundle=_minimal_policy_bundle(),
                event_bus=bus,
            )
        )

        _subscribe_recovery_logger(controller)

        state = _make_state(["claude", "opencode"]).copy_with(last_connectivity_state="online")

        opts = InactivityTimeoutOpts(
            reason=WatchdogFireReason.NO_PROGRESS_QUIET,
            diagnostic={"cumulative": 0.0},
        )
        exc = AgentInactivityTimeoutError("claude", 15.0, opts=opts)

        controller.handle(
            state,
            exc,
            FailureContext(phase="development", agent="claude"),
        )

        fallover_logs = [log for log in logs if "FALLOVER" in log]
        assert len(fallover_logs) >= 1
        assert "watchdog_reason=no_progress_quiet" in fallover_logs[0]

    finally:
        logger.remove(sink_id)


def test_run_loop_guard_suppresses_duplicate_waiting_in_same_phase(
    monkeypatch: MonkeyPatch,
) -> None:
    """Once-per-phase guard suppresses duplicate WAITING emissions until phase changes."""
    policy_bundle = MagicMock()
    policy_bundle.pipeline.terminal_phase = "complete"
    connectivity_monitor = MagicMock()
    connectivity_monitor.current_state = "online"

    ctx = _LoopContext(
        policy_bundle=policy_bundle,
        workspace_scope=MagicMock(),
        config=MagicMock(),
        active_display=MagicMock(),
        display_context=MagicMock(),
        effective_verbosity=0,
        registry=MagicMock(),
        effective_pipeline_subscriber=None,
        controller=MagicMock(),
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=connectivity_monitor,
        sleep=MagicMock(),
        is_quiet=False,
        snapshot_registry=None,
        last_waiting_state_phase=None,
    )

    emitted: list[str] = []

    def mock_emit_activity_line(display: object, phase: str | None, text: str) -> None:
        emitted.append(text)

    monkeypatch.setattr("ralph.pipeline.run_loop.emit_activity_line", mock_emit_activity_line)

    slept: list[float] = []
    ctx.sleep = slept.append
    ctx.active_display = MagicMock()
    ctx.display_context = MagicMock()
    ctx.effective_verbosity = 0
    ctx.registry = MagicMock()
    ctx.effective_pipeline_subscriber = None
    ctx.controller = MagicMock()
    ctx.config_path = None
    ctx.cli_overrides = {}
    ctx.monitor_stop = None
    ctx.pipeline_deps = None

    chain_state = AgentChainState(agents=["claude"], current_index=0, retries=0)
    state = PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    )
    # Use the structured ``is_waiting_state`` flag as the wait-state signal;
    # the previous ``last_error`` text parser was brittle and was replaced
    # with this boolean. The ``last_error`` text remains as operator
    # context only and is NOT a contract the run loop parses.
    state = state.copy_with(
        last_error=(
            "all agents unavailable (last reason: out_of_credits); waiting for cooldown expiry"
        ),
        last_retry_delay_ms=200,
        is_waiting_state=True,
    )
    # The MagicMock controller is consumed by the run loop through the
    # public ``waiting_state_payload`` and ``agents_now_available`` methods.
    ctx.controller.waiting_state_payload.return_value = [("claude", 1, 200)]
    ctx.controller.agents_now_available.return_value = ["claude"]

    call_count = [0]

    def mock_run_pipeline_step(**_kwargs: object) -> PipelineState:
        call_count[0] += 1
        if call_count[0] == 1:
            return state.copy_with(phase="complete")
        return state.copy_with(phase="development")

    monkeypatch.setattr("ralph.pipeline.runner.run_pipeline_step", mock_run_pipeline_step)

    waiting_count_before = len([s for s in emitted if "WAITING" in str(s)])
    _run_inner_loop(state, ctx, prev_phase="development")
    waiting_count_after_first = len([s for s in emitted if "WAITING" in str(s)])
    assert waiting_count_after_first == waiting_count_before + 1, (
        "WAITING should be emitted on first call"
    )

    phase_before = ctx.last_waiting_state_phase

    _run_inner_loop(state.copy_with(phase="complete"), ctx, prev_phase="development")
    waiting_count_after_second = len([s for s in emitted if "WAITING" in str(s)])
    assert waiting_count_after_second == waiting_count_after_first, (
        "WAITING should be suppressed in same phase"
    )
    assert ctx.last_waiting_state_phase == phase_before, "Guard state should be preserved"
