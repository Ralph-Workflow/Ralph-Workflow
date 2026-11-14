from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypedDict
from unittest.mock import MagicMock

if TYPE_CHECKING:
    import pytest
from loguru import logger

from ralph.config.models import UnifiedConfig
from ralph.pipeline.run_loop import _LoopContext, _run_inner_loop
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope


class _LogLevelDouble(Protocol):
    name: str


class _LogRecord(TypedDict):
    level: _LogLevelDouble
    message: str
    extra: dict[str, object]


def _capture_loguru_records() -> tuple[list[_LogRecord], int]:
    records: list[_LogRecord] = []
    sink_id = logger.add(lambda msg: records.append(dict(msg.record)), level="DEBUG")
    return records, sink_id


def _build_recovery_controller_with_unavailable(
    *,
    phase: str,
    agents: list[str],
    unavailable_until_ms: int,
    reason_value: str,
    attempts: dict[str, int] | None = None,
) -> MagicMock:
    attempts = attempts or {}
    timeouts = {f"{phase}:{a}": unavailable_until_ms for a in agents}
    backoff_attempts = {f"{phase}:{a}": attempts.get(a, 1) for a in agents}

    mock_controller = MagicMock()
    mock_controller.snapshot.return_value = {
        "unavailable_timeouts": timeouts,
        "backoff_attempts": backoff_attempts,
    }
    mock_controller.waiting_state_payload.return_value = [
        (a, backoff_attempts[f"{phase}:{a}"], unavailable_until_ms) for a in agents
    ]
    mock_controller.agents_now_available.return_value = list(agents)
    return mock_controller


def _build_loop_context_and_state(
    *,
    mock_controller: MagicMock,
) -> tuple[_LoopContext, PipelineState, list[float], list[str]]:
    policy_bundle = MagicMock()
    policy_bundle.pipeline.terminal_phase = "complete"
    policy_bundle.pipeline.terminal_states.return_value = {"complete", "failed_terminal"}
    connectivity_monitor = MagicMock()
    connectivity_monitor.current_state = "online"

    ctx = _LoopContext(
        policy_bundle=policy_bundle,
        workspace_scope=WorkspaceScope(MemoryWorkspace("/memory/waiting-logs").root),
        config=UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": False}}),
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

    chain_state = AgentChainState(agents=["claude"], current_index=0, retries=0)
    state = PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    )
    state = state.copy_with(
        last_error=(
            "all agents unavailable (last reason: out_of_credits); waiting for cooldown expiry"
        ),
        last_retry_delay_ms=200,
        last_unavailability_reason="out_of_credits",
        is_waiting_state=True,
    )
    slept: list[float] = []
    emitted: list[str] = []
    return ctx, state, slept, emitted


def _drive_run_loop_until_complete(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ctx: _LoopContext,
    state: PipelineState,
    slept: list[float],
    emitted: list[str],
) -> list[_LogRecord]:

    def mock_emit_activity_line(display: object, phase: str | None, text: str) -> None:
        emitted.append(text)

    monkeypatch.setattr("ralph.pipeline.run_loop.emit_activity_line", mock_emit_activity_line)
    ctx.sleep = slept.append

    calls = 0

    def mock_run_pipeline_step(state: PipelineState, **_kwargs: object) -> PipelineState:
        nonlocal calls
        calls += 1
        if calls == 1:
            return state
        return state.copy_with(phase="complete")

    monkeypatch.setattr("ralph.pipeline.runner.run_pipeline_step", mock_run_pipeline_step)
    monkeypatch.setattr("ralph.pipeline.auto_integrate_record.read_record", lambda _root: None)

    records, sink_id = _capture_loguru_records()
    try:
        _run_inner_loop(state, ctx, prev_phase="development")
    finally:
        logger.remove(sink_id)
    return records


def _assert_one_each(emitted: list[str], label: str) -> None:
    matching = [s for s in emitted if label in s]
    assert len(matching) == 1, (
        f"expected exactly one {label} emit, got {len(matching)}: {emitted!r}"
    )


def _assert_waiting_record_shape(records: list[_LogRecord]) -> None:
    waiting_records = [
        r for r in records if "WAITING" in r["message"] and "all agents unavailable" in r["message"]
    ]
    assert len(waiting_records) == 1, (
        f"expected exactly one structured WAITING log, got {len(waiting_records)}"
    )
    waiting_extra = waiting_records[0]["extra"]
    assert waiting_extra.get("recovery") is True
    assert waiting_records[0]["level"].name == "INFO"
    assert waiting_extra.get("phase") == "development"
    assert waiting_extra.get("reason") == "out_of_credits"
    cooldowns = waiting_extra.get("cooldowns")
    assert cooldowns is not None
    claude_tuples = [t for t in cooldowns if t[0] == "claude"]
    assert len(claude_tuples) == 1
    claude_tuple = claude_tuples[0]
    assert claude_tuple[1] == 1
    assert isinstance(claude_tuple[2], int)
    assert 0 <= claude_tuple[2] <= 200
    assert waiting_extra.get("wait_ms") == 200


def _assert_resumed_record_shape(records: list[_LogRecord]) -> None:
    resumed_records = [
        r for r in records if "RESUMED" in r["message"] and "cooldown expired" in r["message"]
    ]
    assert len(resumed_records) == 1, (
        f"expected exactly one structured RESUMED log, got {len(resumed_records)}"
    )
    resumed_extra = resumed_records[0]["extra"]
    assert resumed_extra.get("recovery") is True
    assert resumed_records[0]["level"].name == "INFO"
    assert resumed_extra.get("phase") == "development"
    assert resumed_extra.get("agents") == ["claude"]
    assert resumed_extra.get("reason") == "out_of_credits"
    assert resumed_extra.get("waited_seconds") == 0.2


def _assert_pre_sleep_debug_record(records: list[_LogRecord]) -> None:
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
    assert pre_sleep_extra.get("phase") == "development"
    assert pre_sleep_extra.get("delay_seconds") == 0.2


def test_run_loop_waiting_state_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_controller = _build_recovery_controller_with_unavailable(
        phase="development",
        agents=["claude"],
        unavailable_until_ms=200,
        reason_value="out_of_credits",
        attempts={"claude": 1},
    )
    ctx, state, slept, emitted = _build_loop_context_and_state(
        mock_controller=mock_controller,
    )
    records = _drive_run_loop_until_complete(
        monkeypatch,
        ctx=ctx,
        state=state,
        slept=slept,
        emitted=emitted,
    )
    _assert_one_each(emitted, "WAITING")
    _assert_one_each(emitted, "RESUMED")
    _assert_waiting_record_shape(records)
    _assert_resumed_record_shape(records)
    _assert_pre_sleep_debug_record(records)
    assert len(slept) == 1
    assert slept[0] == 0.2
