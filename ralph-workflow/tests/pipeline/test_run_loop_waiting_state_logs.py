from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    import pytest
from loguru import logger

from ralph.pipeline.run_loop import _LoopContext, _run_inner_loop
from ralph.pipeline.state import AgentChainState, PipelineState


def test_run_loop_waiting_state_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    policy_bundle = MagicMock()
    policy_bundle.pipeline.terminal_phase = "complete"
    connectivity_monitor = MagicMock()
    connectivity_monitor.current_state = "online"

    # Mock RecoveryController and AgentUnavailabilityTracker
    mock_tracker = MagicMock()
    mock_tracker.snapshot.return_value = {
        "unavailable_timeouts": {"development:claude": 5000},
        "backoff_attempts": {"development:claude": 1},
    }
    mock_tracker.is_available.return_value = True
    mock_tracker._clock.monotonic.return_value = 0.0

    mock_controller = MagicMock()
    mock_controller._unavailability_tracker = mock_tracker

    ctx = _LoopContext(
        policy_bundle=policy_bundle,
        workspace_scope=MagicMock(),
        config=MagicMock(),
        active_display=MagicMock(),
        display_context=MagicMock(),
        effective_verbosity=0,
        registry=MagicMock(),
        effective_pipeline_subscriber=None,
        controller=mock_controller,
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

    chain_state = AgentChainState(agents=["claude"], current_index=0, retries=0)
    state = PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    )
    state = state.copy_with(
        last_error="all agents unavailable; waiting for cooldown expiry",
        last_retry_delay_ms=200,
        last_unavailability_reason="out_of_credits",
    )

    calls = 0

    def mock_run_pipeline_step(state: PipelineState, **_kwargs: object) -> PipelineState:
        nonlocal calls
        calls += 1
        if calls == 1:
            return state
        return state.copy_with(phase="complete")

    monkeypatch.setattr("ralph.pipeline.runner.run_pipeline_step", mock_run_pipeline_step)

    logs: list[str] = []
    sink_id = logger.add(logs.append, format="{level} {message}")
    try:
        _run_inner_loop(state, ctx, prev_phase="development")
    finally:
        logger.remove(sink_id)

    # Assertions:
    # 1. exactly one WAITING emit was called
    waiting_emits = [s for s in emitted if "WAITING" in s]
    assert len(waiting_emits) == 1

    # 2. exactly one RESUMED emit was called
    resumed_emits = [s for s in emitted if "RESUMED" in s]
    assert len(resumed_emits) == 1

    # 3. the structured WAITING log was emitted with the expected fields
    waiting_logs = [log for log in logs if "enters WAITING state" in log]
    assert len(waiting_logs) == 1
    assert "INFO" in waiting_logs[0]
    assert "enters WAITING state: all agents unavailable" in waiting_logs[0]
    assert "Last unavailability reason: out_of_credits" in waiting_logs[0]
    assert "Cooldowns: [('claude', 1, 5000)]" in waiting_logs[0]
    assert "Resuming in 200 ms." in waiting_logs[0]

    # 4. the structured RESUMED log was emitted with the expected fields
    resumed_logs = [log for log in logs if "RESUMED: cooldown expired" in log]
    assert len(resumed_logs) == 1
    assert "INFO" in resumed_logs[0]
    assert "Agents now available: ['claude']" in resumed_logs[0]
    assert "Expired reason: out_of_credits" in resumed_logs[0]
    assert "Waited for 0.200 seconds" in resumed_logs[0]

    # 5. ctx.sleep was called exactly once with 0.2
    assert len(slept) == 1
    assert slept[0] == 0.2
