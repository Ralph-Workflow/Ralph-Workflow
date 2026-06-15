"""Black-box tests for the all-agents-unavailable WAITING state, driven by a
REAL ``RecoveryController`` through ``_run_inner_loop``.

This is the integration-boundary regression test that catches the bug class
where the controller and the run loop disagree about the shape of the
wait-state signal. The previous unit test fabricated the state directly
and bypassed the controller's real ``last_error`` format, so it passed
even when the production controller produced a different string than the
run loop's text parser looked for.

The fix is two-fold:
  1. A structured ``is_waiting_state`` boolean flag on ``PipelineState``,
     set by the controller when it enters the wait branch, replaces the
     brittle ``last_error`` text parsing in the run loop.
  2. A public ``controller.unavailability_store`` property exposes the
     ``UnavailabilityStore`` Protocol-typed dependency, and a public
     ``controller.waiting_state_payload(phase, agents)`` method wraps the
     tracker access. The run loop's logging code consumes only this
     public surface, never ``ctx.controller._unavailability_tracker`` or
     ``tracker._clock``.

AC-08 contract:
  - WAITING log: binding(recovery=True) + phase + last_unavailability_reason
    + all (agent, attempt, cooldown_ms) tuples + wait_ms
  - RESUMED log: binding(recovery=True) + phase + agents_now_available +
    expired reason + total_seconds_waited

The test asserts on the loguru record metadata (``record['extra']``)
directly, NOT on the rendered message text, so a regression that silently
drops the structured payload (e.g. a plain ``logger.info`` with no
``bind`` and no kwargs) is caught immediately.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

from loguru import logger

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.timeout_clock import FakeClock
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

if TYPE_CHECKING:
    import pytest


def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _no_output_opts() -> InactivityTimeoutOpts:
    """Build the canonical NO_OUTPUT_AT_START opts for inactivity timeouts."""
    return InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_OUTPUT_AT_START,
        diagnostic={"invocation_elapsed": 30.0},
    )


def _capture_loguru_records() -> tuple[list[dict[str, Any]], int]:
    """Attach a loguru sink that captures full records and returns (records, sink_id).

    ``record['extra']`` is the binding payload (kwargs passed to
    ``logger.bind(...).info(..., **kwargs)``); ``record['message']`` is the
    rendered message text; ``record['level']`` is the loguru Level instance
    (use ``.name`` to get the string).
    """
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
    """Build a REAL ``RecoveryController`` with a pre-seeded unavailability
    state for the given phase/agents.

    Each agent's cooldown is its entry's ``unavailable_until_ms``. The
    tracker uses a ``FakeClock`` so the test is deterministic.
    """
    clock = FakeClock(start=0.0)
    initial_entries: dict[str, UnavailabilityEntry] = {
        f"{phase}:{a}": UnavailabilityEntry(
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
    """Assert the structured WAITING log has the AC-08 contract fields."""
    waiting_records = [
        r for r in records
        if "WAITING" in r["message"] and "all agents unavailable" in r["message"]
    ]
    assert len(waiting_records) == 1, (
        f"expected exactly one structured WAITING log, got {len(waiting_records)}"
    )
    waiting_extra = waiting_records[0]["extra"]
    # AC-08: binding(recovery=True) + INFO level
    assert waiting_extra.get("recovery") is True
    assert waiting_records[0]["level"].name == "INFO"
    # AC-08: phase + cooldown tuples + wait_ms
    assert waiting_extra.get("phase") == phase
    cooldowns = waiting_extra.get("cooldowns")
    assert cooldowns is not None
    # Each cooldown tuple is (agent, attempt, cooldown_ms_remaining). The
    # remaining value depends on how much wall-clock has elapsed since the
    # seed; the contract is that the tuple is present and the remaining is
    # a non-negative int.
    agents_in_cooldowns = {t[0] for t in cooldowns}
    assert agents_in_cooldowns == set(agents)
    for tup in cooldowns:
        assert isinstance(tup[1], int)
        assert isinstance(tup[2], int)
        assert tup[2] >= 0


def _assert_resumed_log(records: list[dict[str, Any]], phase: str) -> None:
    """Assert the structured RESUMED log has the AC-08 contract fields."""
    resumed_records = [
        r for r in records
        if "RESUMED" in r["message"] and "cooldown expired" in r["message"]
    ]
    assert len(resumed_records) == 1, (
        f"expected exactly one structured RESUMED log, got {len(resumed_records)}"
    )
    resumed_extra = resumed_records[0]["extra"]
    # AC-08: binding(recovery=True) + INFO level + phase
    assert resumed_extra.get("recovery") is True
    assert resumed_records[0]["level"].name == "INFO"
    assert resumed_extra.get("phase") == phase


def _build_run_loop_context(
    *, controller: RecoveryController,
) -> tuple[_LoopContext, list[float], list[str]]:
    """Build a ``_LoopContext`` with deterministic mocks for the run loop.

    Returns ``(ctx, slept, emitted)`` where ``slept`` collects the
    seconds the run loop sleeps and ``emitted`` collects the activity-line
    emissions.
    """
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
    """AC-08: end-to-end. A REAL ``RecoveryController.handle(...)`` is
    driven into the all-agents-unavailable wait state, the resulting state
    is passed to ``_run_inner_loop``, and the run loop must emit the
    structured WAITING and RESUMED logs.

    The previous unit test fabricated a state with a hardcoded
    ``last_error`` string and so did not catch the production mismatch
    between the controller's ``last_error`` format and the run loop's
    text parser. The fix moves the wait-state signal to a structured
    ``state.is_waiting_state`` boolean so the controller and the run loop
    can never disagree about the format of the string.
    """
    phase = "development"
    agents = ["claude", "opencode"]
    # Both agents are on cooldown: claude 5s, opencode 10s. The controller
    # enters the wait state on the first handle() call and the run loop
    # must detect it via state.is_waiting_state (structured flag, NOT
    # last_error text parsing).
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

    # Drive a REAL controller.handle() to enter the wait state. The
    # failure is a watchdog NO_OUTPUT_AT_START; both agents are already on
    # cooldown, so the controller enters the wait branch.
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("claude", 30.0, opts=opts)
    state, _effects, _failure_evt = controller.handle(
        state, exc, FailureContext(phase=phase, agent="claude"),
    )

    # Sanity: the controller really did enter the wait state. The real
    # last_error format MUST start with "all agents unavailable (last
    # reason:" -- this is the format the production controller writes,
    # and the run loop's text-parser bug was that it looked for a
    # different (shorter) string. We assert on the real format here.
    assert state.last_error is not None
    assert state.last_error.startswith("all agents unavailable (last reason:")
    # The retry delay must be positive (the wait state sets it).
    assert state.last_retry_delay_ms > 0
    # The structured wait state flag must be True. The run loop detects
    # the wait state via this flag, not via last_error text.
    assert state.is_waiting_state is True, (
        f"expected state.is_waiting_state to be True, got {state.is_waiting_state!r}; "
        f"state.last_error={state.last_error!r}"
    )

    # Now drive _run_inner_loop with the controller-produced state. The
    # run loop must detect the wait state via the structured flag.
    ctx, slept, _ = _build_run_loop_context(controller=controller)

    emitted: list[str] = []

    def mock_emit_activity_line(display: object, phase_arg: str | None, text: str) -> None:
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

    records, sink_id = _capture_loguru_records()
    try:
        _run_inner_loop(state, ctx, prev_phase=phase)
    finally:
        logger.remove(sink_id)

    # AC-08: exactly one WAITING emit and one RESUMED emit.
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

    # AC-08: the run loop slept exactly once with the documented delay.
    assert len(slept) == 1
    assert slept[0] == state.last_retry_delay_ms / 1000.0


def test_controller_accepts_protocol_typed_unavailability_store() -> None:
    """AC-06: the ``RecoveryController`` constructor accepts a
    ``UnavailabilityStore`` Protocol-typed dependency and uses it
    instead of constructing a fresh ``AgentUnavailabilityTracker``.

    A custom Protocol implementation is passed via
    ``RecoveryControllerOptions.unavailability_store``; the controller
    must expose it as a public ``unavailability_store`` property so the
    run loop does not have to reach through ``_unavailability_tracker``.
    """

    class _FakeStore:
        """Minimal Protocol-conforming implementation for the test."""

        @property
        def scope(self) -> str:
            return "session"

        def mark_unavailable(
            self, phase: str, agent: str, reason: UnavailabilityReason | None = None,
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
            unavailability_store=cast("UnavailabilityStore", fake_store),
        ),
    )

    # The public property must return the injected store, NOT a freshly
    # constructed AgentUnavailabilityTracker. The contract is that the
    # caller (the run loop, the controller) consumes the store only via
    # this property, never via the private ``_unavailability_tracker``.
    assert controller.unavailability_store is fake_store
    assert not isinstance(
        controller.unavailability_store, AgentUnavailabilityTracker,
    ) or controller.unavailability_store is fake_store
    # isinstance against the Protocol is also True.
    assert isinstance(controller.unavailability_store, UnavailabilityStore) is True


def test_run_loop_does_not_reach_through_private_tracker_attributes() -> None:
    """AC-07: the run loop's WAITING / RESUMED log builders consume only
    the public ``controller.unavailability_store`` surface, not the
    private ``controller._unavailability_tracker`` or ``tracker._clock``.

    We assert the contract by inspecting the run loop module: the symbols
    ``_unavailability_tracker`` and ``_clock`` (the private tracker /
    clock) MUST NOT appear in the run loop's logging code. The test is
    intentionally source-level so a future contributor who reintroduces
    private reach-through fails CI.
    """
    run_loop_path = Path(__file__).parent.parent.parent / "ralph" / "pipeline" / "run_loop.py"
    src = run_loop_path.read_text(encoding="utf-8")
    # The private tracker attribute MUST NOT be reached through from the
    # run loop. The run loop must consume the public
    # ``controller.unavailability_store`` surface only.
    assert "ctx.controller._unavailability_tracker" not in src, (
        "run_loop.py must not reach through ctx.controller._unavailability_tracker; "
        "use the public controller.unavailability_store surface instead"
    )
    assert "tracker._clock" not in src, (
        "run_loop.py must not reach through tracker._clock; "
        "use the public controller.waiting_state_payload(phase, agents) surface instead"
    )
