from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline import RebaseOutcomeKind, run_loop
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.auto_integrate_recovery_record import continue_retained_resolution
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.retry_diagnostic import RetryDiagnosticHooks
from ralph.pipeline.run_loop import (
    LoopContext,
    handle_pending_retry,
    integration_alert_for_state,
)
from ralph.pipeline.state import PipelineState
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.timeout_seconds(15)

_RESOLUTION_PHASE = "rebase_conflict_resolution"


@contextlib.contextmanager
def _captured_logs() -> Iterator[list[str]]:
    logs: list[str] = []

    def sink(msg: Any) -> None:
        logs.append(str(msg))

    sink_id = logger.add(sink, level="INFO", format="{message}")
    try:
        yield logs
    finally:
        logger.remove(sink_id)


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


def test_resolution_phase_launch_gate_closed_suppresses_selection_evidence() -> None:
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, clock=FakeClock(start=200.0))
    )
    controller.set_phase_launch_gate(_RESOLUTION_PHASE, available=False)

    with _captured_logs() as logs:
        selection = controller.preferred_agent_index(_RESOLUTION_PHASE, ["claude", "opencode"])

    assert selection.index == 0
    assert selection.agent == "claude"
    assert not any("Selected agent" in log for log in logs)
    assert not any("No selectable agent" in log for log in logs)

    with _captured_logs() as other_logs:
        controller.preferred_agent_index("development", ["claude"])

    assert any("Phase development: Selected agent claude" in log for log in other_logs)


def test_resolution_phase_launch_gate_open_emits_selection_evidence() -> None:
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, clock=FakeClock(start=200.0))
    )
    controller.set_phase_launch_gate(_RESOLUTION_PHASE, available=True)

    with _captured_logs() as logs:
        selection = controller.preferred_agent_index(_RESOLUTION_PHASE, ["claude", "opencode"])

    assert selection.index == 0
    assert selection.agent == "claude"
    assert any(f"Phase {_RESOLUTION_PHASE}: Selected agent claude" in log for log in logs)


def test_continuation_seam_sets_launch_gate_from_resolver_availability(tmp_path: Path) -> None:
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, clock=FakeClock(start=200.0))
    )
    record = IntegrationRecord(
        phase="integrating",
        target="",
        pre_feature_sha="feature-sha",
        pre_target_sha=None,
    )

    blocked = continue_retained_resolution(
        tmp_path,
        record,
        None,
        None,
        None,
        recovery_controller=controller,
    )

    assert isinstance(blocked, str)
    assert controller.phase_launch_available(_RESOLUTION_PHASE) is False

    available = continue_retained_resolution(
        tmp_path,
        record,
        None,
        lambda *_args, **_kwargs: False,
        lambda *_args, **_kwargs: False,
        recovery_controller=controller,
    )

    assert isinstance(available, str)
    assert controller.phase_launch_available(_RESOLUTION_PHASE) is True


def test_integration_alert_for_config_failure_names_config_file_and_command(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True)
    cfg_file = agent_dir / "ralph-workflow.toml"
    cfg_file.write_text("[general]\n", encoding="utf-8")

    rebase = RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        last_reason="rebase resolution remains pending after continuation (cause: CONFIG_FAILURE)",
        last_failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
    )
    state = PipelineState(phase="planning", rebase=rebase)

    alert = integration_alert_for_state(state, root=tmp_path)

    assert alert is not None
    assert "CONFIG_FAILURE" in alert
    assert "ralph --check-config" in alert
    assert str(cfg_file) in alert
    assert "unresolved conflict" not in alert


def test_continuation_config_repair_triggers_single_retry(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True)
    cfg_file = agent_dir / "ralph-workflow.toml"
    cfg_file.write_text("invalid_toml = [broken", encoding="utf-8")

    continuation_failure = RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        last_reason="rebase resolution remains pending after continuation (cause: CONFIG_FAILURE)",
        last_failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
    )
    paused_failure = RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        last_reason="paused rebase configuration failure",
        last_failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
    )

    clock = FakeClock(start=0.0)
    sleeps: list[float] = []
    policy_bundle = MagicMock()
    policy_bundle.pipeline.terminal_phase = "complete"
    policy_bundle.pipeline.terminal_states.return_value = {"complete", "failed_terminal"}
    connectivity_monitor = MagicMock()
    connectivity_monitor.current_state = "online"
    ctx = LoopContext(
        policy_bundle=policy_bundle,
        workspace_scope=WorkspaceScope(root=tmp_path),
        config=UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": False}}),
        active_display=MagicMock(),
        display_context=MagicMock(),
        effective_verbosity=0,
        registry=MagicMock(),
        effective_pipeline_subscriber=None,
        controller=RecoveryController(options=RecoveryControllerOptions(clock=clock)),
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=connectivity_monitor,
        sleep=sleeps.append,
        is_quiet=False,
        latest_state=[],
        hooks=RetryDiagnosticHooks(recovery_preamble=lambda *_: continuation_failure),
    )

    state = PipelineState(phase="planning", rebase=paused_failure)

    with _captured_logs() as logs:
        first = handle_pending_retry(
            state,
            ctx,
            paused_failure,
            detail="continuation configuration failure",
            backend=MagicMock(),
        )

    assert sleeps == [5.0]
    assert not any("retrying continuation" in log for log in logs)

    cfg_file.write_text("[general]\n", encoding="utf-8")

    with _captured_logs() as logs:
        second = handle_pending_retry(
            first,
            ctx,
            paused_failure,
            detail="continuation configuration repaired",
            backend=MagicMock(),
        )

    assert sleeps == [5.0]
    assert second.wait_reason is None
    assert any(
        "configuration failure self-recovery:" in log and "repaired; retrying continuation" in log
        for log in logs
    )

    with _captured_logs() as logs:
        handle_pending_retry(
            second,
            ctx,
            paused_failure,
            detail="continuation unchanged after repair",
            backend=MagicMock(),
        )

    assert sleeps == [5.0, 5.0]
    assert not any("retrying continuation" in log for log in logs)
