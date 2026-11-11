from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.pipeline import run_loop
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.state import PipelineState
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    import pytest


def _entry(until_ms: int) -> UnavailabilityEntry:
    return UnavailabilityEntry(
        unavailable_until_ms=until_ms,
        reason=UnavailabilityReason.NO_OUTPUT_AT_START,
        attempt=0,
        base_backoff_ms=5000,
        max_backoff_ms=5000,
    )


def test_run_loop_reselects_preferred_agent_on_phase_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    policy_bundle.pipeline.terminal_states.return_value = {"complete", "failed_terminal"}
    connectivity_monitor = MagicMock()
    connectivity_monitor.current_state = "online"
    workspace = MemoryWorkspace("/memory/phase-selection")
    monkeypatch.setattr("ralph.pipeline.auto_integrate_record.read_record", lambda _root: None)
    ctx = run_loop._LoopContext(
        policy_bundle=policy_bundle,
        workspace_scope=WorkspaceScope(workspace.root),
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

    selection_first = controller.preferred_agent_index("development", ["claude", "opencode", "agy"])
    assert selection_first.index == 0
    assert selection_first.agent == "claude"

    selection_second = controller.preferred_agent_index(
        "development", ["claude", "opencode", "agy"]
    )
    assert selection_second.index == 0
    assert selection_second.agent == "claude"

    selection_third = controller.preferred_agent_index("development", ["claude", "opencode", "agy"])
    assert selection_third.index == 0
    assert selection_third.agent == "claude"


def test_phase_entry_picks_highest_priority_when_cursor_points_past_index_zero(
    monkeypatch: object,
) -> None:
    from unittest.mock import MagicMock

    from ralph.agents.timeout_clock import FakeClock
    from ralph.pipeline import run_loop
    from ralph.pipeline.agent_chain_state import AgentChainState
    from ralph.pipeline.state import PipelineState

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
    policy_bundle.pipeline.terminal_states.return_value = {"complete", "failed_terminal"}
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
                retries=5,
            )
        },
    )

    selected = run_loop._reselect_preferred_agent(state, ctx)
    chain = selected.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 0
    assert chain.retries == 0
    assert selected.last_agent_session_id is None
    assert selected.agent_retry_intent.action is None


def test_phase_entry_skips_higher_priority_in_cooldown_returns_to_lower_priority(
    monkeypatch: object,
) -> None:
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
                "claude": _entry(10_000),
            },
        )
    )

    policy_bundle = MagicMock()
    policy_bundle.pipeline.terminal_phase = "complete"
    policy_bundle.pipeline.terminal_states.return_value = {"complete", "failed_terminal"}
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

    selected = run_loop._reselect_preferred_agent(state, ctx)
    chain = selected.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1
    assert chain.agents[chain.current_index] == "opencode"


def test_phase_entry_returns_to_highest_priority_after_cooldown_expiry(
    monkeypatch: object,
) -> None:
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
    policy_bundle.pipeline.terminal_states.return_value = {"complete", "failed_terminal"}
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
                current_index=1,
                retries=2,
            )
        },
    )

    first = run_loop._reselect_preferred_agent(state, ctx)
    first_chain = first.chain_for_phase("development")
    assert first_chain is not None
    assert first_chain.current_index == 1

    clock.advance(6.0)

    second = run_loop._reselect_preferred_agent(first, ctx)
    second_chain = second.chain_for_phase("development")
    assert second_chain is not None
    assert second_chain.current_index == 0
    assert second_chain.agents[second_chain.current_index] == "claude"


def test_phase_entry_all_agents_unavailable_waits_with_priority_first_ordering(
    monkeypatch: object,
) -> None:
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
                "opencode": _entry(5000),
                "agy": _entry(12_000),
            },
        )
    )

    policy_bundle = MagicMock()
    policy_bundle.pipeline.terminal_phase = "complete"
    policy_bundle.pipeline.terminal_states.return_value = {"complete", "failed_terminal"}
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

    waited = run_loop._reselect_preferred_agent(state, ctx)
    chain = waited.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 0
    assert waited.is_waiting_state is True
    assert waited.last_retry_delay_ms == 5000
