"""Regression test for preferred agent re-selection on phase entry."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline import run_loop
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.state import PipelineState
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason


def _entry(until_ms: int) -> UnavailabilityEntry:
    return UnavailabilityEntry(
        unavailable_until_ms=until_ms,
        reason=UnavailabilityReason.NO_OUTPUT_AT_START,
        attempt=0,
        base_backoff_ms=5000,
        max_backoff_ms=5000,
    )


def test_run_loop_reselects_preferred_agent_on_phase_entry(
    monkeypatch: Any,
) -> None:
    # Clock at 200s (200,000ms), cooldowns expired at 5000ms.
    clock = FakeClock(start=200.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            unavailability_entries={
                "development:claude": _entry(5000),
                "development:opencode": _entry(5000),
                "development:agy": _entry(5000),
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
        phase="planning",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode", "agy"],
                current_index=2,
                retries=1,
            )
        },
    )

    seen_states: list[PipelineState] = []

    def run_step(*, state: PipelineState, **_kwargs: object) -> PipelineState:
        seen_states.append(state)
        if len(seen_states) == 1:
            return state.copy_with(phase="development")
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
        run_loop._run_inner_loop(state, ctx, prev_phase=None)
    finally:
        logger.remove(sink_id)

    assert len(seen_states) == 2
    dev_state = seen_states[1]
    assert str(dev_state.phase) == "development"
    dev_chain = dev_state.chain_for_phase("development")
    assert dev_chain is not None
    assert dev_chain.current_index == 0
    assert dev_chain.retries == 0
    assert dev_state.last_agent_session_id is None
    assert any("Phase development: Selected agent claude" in log for log in logs)


def test_controller_preferred_agent_index_repeated_invocations() -> None:
    """Plan S-2: priority selection is the source of truth on every invocation.

    Two back-to-back calls to ``preferred_agent_index`` both pick the
    highest-priority available agent. The selection is stable across
    invocations -- the cursor from the prior call is not carried forward.
    """
    clock = FakeClock(start=200.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            unavailability_entries={
                "development:claude": _entry(5000),
                "development:opencode": _entry(5000),
                "development:agy": _entry(5000),
            },
        )
    )

    # First invocation: all cooldowns expired; claude (index 0) wins.
    selection_first = controller.preferred_agent_index(
        "development", ["claude", "opencode", "agy"]
    )
    assert selection_first.index == 0
    assert selection_first.agent == "claude"

    # Second invocation: claude is still the highest-priority available agent.
    selection_second = controller.preferred_agent_index(
        "development", ["claude", "opencode", "agy"]
    )
    assert selection_second.index == 0
    assert selection_second.agent == "claude"

    # Third invocation: same answer again. Priority is not a cursor.
    selection_third = controller.preferred_agent_index(
        "development", ["claude", "opencode", "agy"]
    )
    assert selection_third.index == 0
    assert selection_third.agent == "claude"



# ---------------------------------------------------------------------------
# Phase-entry priority-first invariants (plan S-1)
#
# These focused regressions assert that the run loop's phase-entry
# ``_reselect_preferred_agent`` always scans the chain from index 0
# regardless of the persisted ``current_index``, that successive phase
# entries keep priority-first selection, and that the only contract
# the phase-entry selection changes (``current_index``, ``retries``,
# session id, retry intent) is the same one every priority-first
# selection change requires.
# ---------------------------------------------------------------------------


def test_phase_entry_picks_highest_priority_when_cursor_points_past_index_zero(
    monkeypatch: object,
) -> None:
    """Phase entry where persisted current_index = N picks index 0 when cooldowns expired.

    The persisted current_index is informational, NOT a search origin.
    When a phase is entered (e.g. after a phase transition), the
    reselection must consult the chain from index 0 and pick the
    highest-priority available agent. A cursor-style implementation
    would advance past index 0 here.
    """
    from unittest.mock import MagicMock

    from ralph.agents.timeout_clock import FakeClock
    from ralph.pipeline import run_loop
    from ralph.pipeline.agent_chain_state import AgentChainState
    from ralph.pipeline.state import PipelineState

    # Far past all cooldowns (cooldowns expire at 5_000ms).
    clock = FakeClock(start=200.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            unavailability_entries={
                "claude": _entry(5000),
                "opencode": _entry(5000),
                "agy": _entry(5000),
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

    # Persisted current_index points past 0; cooldowns are expired.
    # state.phase matches the chain phase so _reselect_preferred_agent
    # finds the chain via state.chain_for_phase(state.phase).
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode", "agy"],
                current_index=2,
                retries=5,
            )
        },
    )

    # Call the run loop's reselect seam directly.
    selected = run_loop._reselect_preferred_agent(state, ctx)
    chain = selected.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 0
    assert chain.retries == 0
    # Session id and retry intent must be cleared on selection change.
    assert selected.last_agent_session_id is None
    assert selected.agent_retry_intent.action is None


def test_phase_entry_skips_higher_priority_in_cooldown_returns_to_lower_priority(
    monkeypatch: object,
) -> None:
    """Phase entry with highest-priority agent on cooldown picks the next-lowest-index.

    Priority-first selection with cooldown: index 0 is on cooldown,
    the scan skips it and picks the next selectable agent. The cursor
    from a prior invocation is not used.
    """
    from unittest.mock import MagicMock

    from ralph.agents.timeout_clock import FakeClock
    from ralph.pipeline import run_loop
    from ralph.pipeline.agent_chain_state import AgentChainState
    from ralph.pipeline.state import PipelineState

    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            # Only claude is on cooldown; opencode and agy are available.
            unavailability_entries={
                "claude": _entry(10_000),  # 10s cooldown
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
                current_index=0,
                retries=0,
            )
        },
    )

    # claude is on cooldown -> opencode (index 1) wins.
    selected = run_loop._reselect_preferred_agent(state, ctx)
    chain = selected.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1
    assert chain.agents[chain.current_index] == "opencode"


def test_phase_entry_returns_to_highest_priority_after_cooldown_expiry(
    monkeypatch: object,
) -> None:
    """Phase entry after cooldown expiry restores the highest-priority agent.

    The cursor from the previous selection is not used. Even though
    the persisted current_index points at opencode, once claude's
    cooldown expires the next phase entry picks claude.
    """
    from unittest.mock import MagicMock

    from ralph.agents.timeout_clock import FakeClock
    from ralph.pipeline import run_loop
    from ralph.pipeline.agent_chain_state import AgentChainState
    from ralph.pipeline.state import PipelineState

    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            unavailability_entries={
                "claude": _entry(5000),
                "opencode": _entry(5000),
                "agy": _entry(5000),
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

    # Persisted current_index points at opencode (index 1).
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode", "agy"],
                current_index=1,
                retries=2,
            )
        },
    )

    # First phase entry: claude on cooldown, opencode wins.
    first = run_loop._reselect_preferred_agent(state, ctx)
    first_chain = first.chain_for_phase("development")
    assert first_chain is not None
    assert first_chain.current_index == 1

    # Advance past claude's cooldown.
    clock.advance(6.0)

    # Second phase entry: claude's cooldown expired, claude wins even
    # though the persisted current_index points at opencode.
    second = run_loop._reselect_preferred_agent(first, ctx)
    second_chain = second.chain_for_phase("development")
    assert second_chain is not None
    assert second_chain.current_index == 0
    assert second_chain.agents[second_chain.current_index] == "claude"


def test_phase_entry_all_agents_unavailable_waits_with_priority_first_ordering(
    monkeypatch: object,
) -> None:
    """When every agent is on cooldown, phase entry sets is_waiting_state with the earliest cooldown.

    The wait delay is the smallest remaining cooldown across the
    chain (priority-first ordering of cooldowns). A cursor-style
    implementation would use the cursor's agent cooldown only.
    """
    from unittest.mock import MagicMock

    from ralph.agents.timeout_clock import FakeClock
    from ralph.pipeline import run_loop
    from ralph.pipeline.agent_chain_state import AgentChainState
    from ralph.pipeline.state import PipelineState

    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            unavailability_entries={
                "claude": _entry(8000),
                "opencode": _entry(5000),  # earliest
                "agy": _entry(12_000),
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
                current_index=0,
                retries=0,
            )
        },
    )

    # Every agent is on cooldown; phase entry sets the wait state.
    waited = run_loop._reselect_preferred_agent(state, ctx)
    # The cursor at index 0 is preserved; selection found no agent.
    chain = waited.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 0
    # Wait state is set with the earliest cooldown (5_000ms).
    assert waited.is_waiting_state is True
    assert waited.last_retry_delay_ms == 5000
