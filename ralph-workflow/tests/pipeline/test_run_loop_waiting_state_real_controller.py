from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from loguru import logger

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.pipeline.integration_resolution_status import IntegrationResolutionStatus
from ralph.pipeline.integration_resolution_types import IntegrationResolutionVerdict
from ralph.pipeline.run_loop import _LoopContext, _run_inner_loop
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_unavailability_tracker import (
    AgentUnavailabilityTracker,
    UnavailabilityEntry,
    UnavailabilityStore,
)
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEventBus
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    import pytest


def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _no_output_opts() -> InactivityTimeoutOpts:
    return InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_OUTPUT_AT_START,
        diagnostic={"invocation_elapsed": 30.0},
    )


def _capture_loguru_records() -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    sink_id = logger.add(lambda msg: records.append(dict(msg.record)), level="DEBUG")
    return records, sink_id


def _build_real_controller_with_unavailable(
    *,
    phase: str,
    agents: list[str],
    unavailable_until_ms_by_agent: dict[str, int],
    reason: UnavailabilityReason,
) -> RecoveryController:
    clock = FakeClock(start=0.0)
    initial_entries: dict[str, UnavailabilityEntry] = {
        a: UnavailabilityEntry(
            unavailable_until_ms=unavailable_until_ms_by_agent[a],
            reason=reason,
            attempt=0,
            base_backoff_ms=60_000 if reason == UnavailabilityReason.OUT_OF_CREDITS else 5_000,
            max_backoff_ms=1_800_000 if reason == UnavailabilityReason.OUT_OF_CREDITS else 30_000,
        )
        for a in agents
    }
    return RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
            unavailability_entries=initial_entries,
        ),
    )


def _assert_waiting_log(records: list[dict[str, Any]], phase: str, agents: list[str]) -> None:
    waiting_records = [
        r for r in records if "WAITING" in r["message"] and "all agents unavailable" in r["message"]
    ]
    assert len(waiting_records) == 1, (
        f"expected exactly one structured WAITING log, got {len(waiting_records)}"
    )
    waiting_extra = waiting_records[0]["extra"]
    assert waiting_extra.get("recovery") is True
    assert waiting_records[0]["level"].name == "INFO"
    assert waiting_extra.get("phase") == phase
    cooldowns = waiting_extra.get("cooldowns")
    assert cooldowns is not None
    agents_in_cooldowns = {t[0] for t in cooldowns}
    assert agents_in_cooldowns == set(agents)
    for tup in cooldowns:
        assert isinstance(tup[1], int)
        assert isinstance(tup[2], int)
        assert tup[2] >= 0


def _assert_resumed_log(records: list[dict[str, Any]], phase: str) -> None:
    resumed_records = [
        r for r in records if "RESUMED" in r["message"] and "cooldown expired" in r["message"]
    ]
    assert len(resumed_records) == 1, (
        f"expected exactly one structured RESUMED log, got {len(resumed_records)}"
    )
    resumed_extra = resumed_records[0]["extra"]
    assert resumed_extra.get("recovery") is True
    assert resumed_records[0]["level"].name == "INFO"
    assert resumed_extra.get("phase") == phase


def _build_run_loop_context(
    *,
    controller: RecoveryController,
) -> tuple[_LoopContext, list[float], list[str]]:
    policy_bundle = MagicMock()
    policy_bundle.pipeline.terminal_phase = "complete"
    policy_bundle.pipeline.terminal_states.return_value = {"complete"}
    connectivity_monitor = MagicMock()
    connectivity_monitor.current_state = "online"
    ctx = _LoopContext(
        policy_bundle=policy_bundle,
        workspace_scope=WorkspaceScope(MemoryWorkspace("/memory/waiting-controller").root),
        config=UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": False}}),
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
        sleep=MagicMock(),
        is_quiet=False,
        snapshot_registry=None,
        last_waiting_state_phase=None,
    )
    slept: list[float] = []
    ctx.sleep = slept.append
    return ctx, slept, []


def test_real_controller_wait_state_emits_waiting_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase = "development"
    agents = ["claude", "opencode"]
    controller = _build_real_controller_with_unavailable(
        phase=phase,
        agents=agents,
        unavailable_until_ms_by_agent={"claude": 5_000, "opencode": 10_000},
        reason=UnavailabilityReason.OUT_OF_CREDITS,
    )

    chain_state = AgentChainState(agents=agents, current_index=0, retries=0)
    state = PipelineState(phase=phase, phase_chains={phase: chain_state}).copy_with(
        last_connectivity_state="online",
    )

    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("claude", 30.0, opts=opts)
    state, _effects, _failure_evt = controller.handle(
        state,
        exc,
        FailureContext(phase=phase, agent="claude"),
    )

    assert state.last_error is not None
    assert state.last_error.startswith("all agents unavailable (last reason:")
    assert state.last_retry_delay_ms > 0
    assert state.is_waiting_state is True, (
        f"expected state.is_waiting_state to be True, got {state.is_waiting_state!r}; "
        f"state.last_error={state.last_error!r}"
    )

    ctx, slept, _ = _build_run_loop_context(controller=controller)

    emitted: list[str] = []

    def mock_emit_activity_line(display: object, _phase_arg: str | None, text: str) -> None:
        emitted.append(text)

    monkeypatch.setattr("ralph.pipeline.run_loop.emit_activity_line", mock_emit_activity_line)

    calls = 0

    def mock_run_pipeline_step(state: PipelineState, **_kwargs: object) -> PipelineState:
        nonlocal calls
        calls += 1
        if calls == 1:
            return state
        return state.copy_with(phase="complete")

    monkeypatch.setattr("ralph.pipeline.runner.run_pipeline_step", mock_run_pipeline_step)
    monkeypatch.setattr("ralph.pipeline.auto_integrate_record.read_record", lambda _root: None)
    monkeypatch.setattr(
        "ralph.pipeline.run_loop.inspect_integration_resolution",
        lambda *_a, **_k: IntegrationResolutionVerdict(
            status=IntegrationResolutionStatus.RESOLVED,
        ),
    )

    records, sink_id = _capture_loguru_records()
    try:
        _run_inner_loop(state, ctx, prev_phase=phase)
    finally:
        logger.remove(sink_id)

    waiting_emits = [s for s in emitted if "WAITING" in s]
    assert len(waiting_emits) == 1, (
        f"expected exactly one WAITING emit, got {len(waiting_emits)}: {emitted!r}"
    )
    resumed_emits = [s for s in emitted if "RESUMED" in s]
    assert len(resumed_emits) == 1, (
        f"expected exactly one RESUMED emit, got {len(resumed_emits)}: {emitted!r}"
    )

    _assert_waiting_log(records, phase, agents)
    _assert_resumed_log(records, phase)

    assert len(slept) == 1
    assert slept[0] == state.last_retry_delay_ms / 1000.0

    pre_sleep_records = [
        r
        for r in records
        if r["level"].name == "DEBUG" and "Starting cooldown sleep" in r["message"]
    ]
    assert len(pre_sleep_records) == 1, (
        f"expected exactly one structured pre-sleep DEBUG log, "
        f"got {len(pre_sleep_records)}: {[r['message'] for r in pre_sleep_records]!r}"
    )
    pre_sleep_extra = pre_sleep_records[0]["extra"]
    assert pre_sleep_extra.get("recovery") is True, (
        f"pre-sleep DEBUG log must carry recovery=True binding, got extra={pre_sleep_extra!r}"
    )
    assert pre_sleep_extra.get("phase") == phase
    assert pre_sleep_extra.get("delay_seconds") == state.last_retry_delay_ms / 1000.0


def test_controller_accepts_protocol_typed_unavailability_store() -> None:

    class _FakeStore:
        @property
        def scope(self) -> str:
            return "session"

        def mark_unavailable(
            self,
            phase: str,
            agent: str,
            reason: UnavailabilityReason | None = None,
        ) -> UnavailabilityEntry:
            return UnavailabilityEntry(
                unavailable_until_ms=200,
                reason=reason,
                attempt=0,
                base_backoff_ms=60_000,
                max_backoff_ms=1_800_000,
            )

        def is_available(self, phase: str, agent: str) -> bool:
            return False

        def earliest_unavailable_wait_ms(self, phase: str, agents: list[str]) -> int:
            return 200

        def reset_backoff(self, phase: str, agent: str) -> None:
            return None

        def snapshot(self) -> dict[str, dict[str, object]]:
            return {
                "unavailable_timeouts": {"development:claude": 200},
                "backoff_attempts": {"development:claude": 1},
            }

    fake_store = _FakeStore()
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            policy_bundle=_minimal_policy_bundle(),
            unavailability_store=fake_store,
        ),
    )

    assert controller.unavailability_store is fake_store
    assert (
        not isinstance(
            controller.unavailability_store,
            AgentUnavailabilityTracker,
        )
        or controller.unavailability_store is fake_store
    )
    assert isinstance(controller.unavailability_store, UnavailabilityStore) is True


def test_run_loop_does_not_reach_through_private_tracker_attributes() -> None:
    run_loop_path = Path(__file__).parent.parent.parent / "ralph" / "pipeline" / "run_loop.py"
    src = run_loop_path.read_text(encoding="utf-8")
    assert "ctx.controller._unavailability_tracker" not in src, (
        "run_loop.py must not reach through ctx.controller._unavailability_tracker; "
        "use the public controller.unavailability_store surface instead"
    )
    assert "tracker._clock" not in src, (
        "run_loop.py must not reach through tracker._clock; "
        "use the public controller.waiting_state_payload(phase, agents) surface instead"
    )
