"""Black-box tests for the all-agents-unavailable WAITING state structured logs.

The run loop emits structured loguru INFO logs (with ``binding(recovery=True)``)
on every WAITING/RESUMED transition. These logs are the operator-visible
diagnostic surface for the never-crash wait state, so the structured fields
(phase, reason, cooldown tuples, wait_ms, agents_now_available,
waited_seconds) are part of the public contract.

AC-08 contract:
  - WAITING log: binding(recovery=True) + phase + last_unavailability_reason
    + all (agent, attempt, cooldown_ms) tuples + wait_ms
  - RESUMED log: binding(recovery=True) + phase + agents_now_available +
    expired reason + total_seconds_waited

The previous version of this test only asserted on the rendered message text
and depended on private mock attributes (``mock_controller._unavailability_tracker``,
``mock_tracker._clock``). That implementation would miss a regression where
the structured ``extra`` payload disappears (e.g. someone replaces
``logger.bind(recovery=True).info(..., phase=..., reason=..., ...)`` with a
plain ``logger.info(plain_string)``) while similar-looking text remains.

The new implementation captures the loguru record metadata directly via a
sink callback, asserts on the ``record['extra']`` dict (the binding payload),
and uses the public surface (``controller.snapshot()``) to seed the
unavailability state instead of poking private tracker attributes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

if TYPE_CHECKING:
    import pytest
from loguru import logger

from ralph.pipeline.run_loop import _LoopContext, _run_inner_loop
from ralph.pipeline.state import AgentChainState, PipelineState


def _capture_loguru_records() -> tuple[list[dict[str, Any]], int]:
    """Attach a loguru sink that captures full records and returns (records, sink_id).

    The captured records are dicts; ``record['extra']`` is the binding payload
    (kwargs passed to ``logger.bind(...).info(..., **kwargs)``), ``record['message']``
    is the rendered message text, and ``record['level']`` is the loguru Level
    instance (use ``.name`` to get the string).
    """
    records: list[dict[str, Any]] = []
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
    """Build a MagicMock RecoveryController whose public surface
    (``waiting_state_payload`` and ``agents_now_available``) reports a
    deterministic unavailable state for the given phase/agents.

    The run loop consumes the unavailability state only through these
    two public controller methods; the private ``_unavailability_tracker``
    attribute is no longer touched. The mock's ``waiting_state_payload``
    returns ``[(agent, attempt, cooldown_ms_remaining)]`` tuples with
    ``cooldown_ms_remaining = unavailable_until_ms`` (the run loop
    contract is non-negative, so the test asserts on a range, not the
    exact value).
    """
    attempts = attempts or {}
    timeouts = {f"{phase}:{a}": unavailable_until_ms for a in agents}
    backoff_attempts = {f"{phase}:{a}": attempts.get(a, 1) for a in agents}

    mock_controller = MagicMock()
    mock_controller.snapshot.return_value = {
        "unavailable_timeouts": timeouts,
        "backoff_attempts": backoff_attempts,
    }
    # Public surface only: the run loop calls these two methods. The
    # implementation detail of how the controller derives the payload
    # is irrelevant to the test; the run loop consumes only the public
    # surface.
    mock_controller.waiting_state_payload.return_value = [
        (a, backoff_attempts[f"{phase}:{a}"], unavailable_until_ms) for a in agents
    ]
    mock_controller.agents_now_available.return_value = list(agents)
    return mock_controller


def test_run_loop_waiting_state_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-08: the run loop emits structured WAITING and RESUMED logs with
    ``binding(recovery=True)`` and the documented field set, when the
    controller enters and exits the all-agents-unavailable wait state.

    This test asserts on the loguru record metadata (``record['extra']``)
    directly, not just the rendered message text, so a regression that
    silently drops the structured payload (e.g. a plain ``logger.info``
    with no ``bind`` and no kwargs) is caught immediately.
    """
    policy_bundle = MagicMock()
    policy_bundle.pipeline.terminal_phase = "complete"
    connectivity_monitor = MagicMock()
    connectivity_monitor.current_state = "online"

    mock_controller = _build_recovery_controller_with_unavailable(
        phase="development",
        agents=["claude"],
        unavailable_until_ms=200,
        reason_value="out_of_credits",
        attempts={"claude": 1},
    )

    ctx = _LoopContext(
        policy_bundle=policy_bundle,
        workspace_scope=MagicMock(),
        config=MagicMock(),
        active_display=MagicMock(),
        display_context=MagicMock(),
        effective_verbosity=0,
        registry=MagicMock(),
        effective_pipeline_subscriber=None,
        controller=cast("Any", mock_controller),
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
    # Use the structured ``is_waiting_state`` flag as the wait-state signal;
    # the previous ``last_error`` text parser was brittle and was replaced
    # with this boolean. The ``last_error`` text remains as operator
    # context only and is NOT a contract the run loop parses.
    state = state.copy_with(
        last_error=(
            "all agents unavailable (last reason: out_of_credits);"
            " waiting for cooldown expiry"
        ),
        last_retry_delay_ms=200,
        last_unavailability_reason="out_of_credits",
        is_waiting_state=True,
    )

    calls = 0

    def mock_run_pipeline_step(state: PipelineState, **_kwargs: object) -> PipelineState:
        nonlocal calls
        calls += 1
        if calls == 1:
            return state
        return state.copy_with(phase="complete")

    monkeypatch.setattr("ralph.pipeline.runner.run_pipeline_step", mock_run_pipeline_step)

    records, sink_id = _capture_loguru_records()
    try:
        _run_inner_loop(state, ctx, prev_phase="development")
    finally:
        logger.remove(sink_id)

    # 1. Display-level: exactly one WAITING emit and one RESUMED emit.
    waiting_emits = [s for s in emitted if "WAITING" in s]
    assert len(waiting_emits) == 1
    resumed_emits = [s for s in emitted if "RESUMED" in s]
    assert len(resumed_emits) == 1

    # 2. Structured WAITING log: binding(recovery=True) + documented fields.
    waiting_records = [
        r
        for r in records
        if "WAITING" in r["message"] and "all agents unavailable" in r["message"]
    ]
    assert len(waiting_records) == 1, (
        f"expected exactly one structured WAITING log, got {len(waiting_records)}"
    )
    waiting_extra = waiting_records[0]["extra"]
    # AC-08: binding(recovery=True)
    assert waiting_extra.get("recovery") is True
    assert waiting_records[0]["level"].name == "INFO"
    # AC-08: phase + last_unavailability_reason + cooldown tuples + wait_ms
    assert waiting_extra.get("phase") == "development"
    assert waiting_extra.get("reason") == "out_of_credits"
    cooldowns = waiting_extra.get("cooldowns")
    assert cooldowns is not None
    # Each cooldown tuple is (agent, attempt, cooldown_ms_remaining). The
    # remaining value depends on how much wall-clock has elapsed since the
    # seed (the run loop reads monotonic ms from the controller's clock);
    # the contract is that the tuple is present and the remaining is a
    # non-negative int <= the seeded unavailable_until_ms.
    claude_tuples = [t for t in cooldowns if t[0] == "claude"]
    assert len(claude_tuples) == 1
    claude_tuple = claude_tuples[0]
    assert claude_tuple[1] == 1
    assert isinstance(claude_tuple[2], int)
    assert 0 <= claude_tuple[2] <= 200
    assert waiting_extra.get("wait_ms") == 200

    # 3. Structured RESUMED log: binding(recovery=True) + documented fields.
    resumed_records = [
        r for r in records if "RESUMED" in r["message"] and "cooldown expired" in r["message"]
    ]
    assert len(resumed_records) == 1, (
        f"expected exactly one structured RESUMED log, got {len(resumed_records)}"
    )
    resumed_extra = resumed_records[0]["extra"]
    # AC-08: binding(recovery=True)
    assert resumed_extra.get("recovery") is True
    assert resumed_records[0]["level"].name == "INFO"
    # AC-08: phase + agents_now_available + expired reason + total_seconds_waited
    assert resumed_extra.get("phase") == "development"
    assert resumed_extra.get("agents") == ["claude"]
    assert resumed_extra.get("reason") == "out_of_credits"
    assert resumed_extra.get("waited_seconds") == 0.2

    # 4. The run loop slept exactly once with the documented delay.
    assert len(slept) == 1
    assert slept[0] == 0.2
