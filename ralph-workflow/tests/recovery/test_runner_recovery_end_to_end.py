from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.config.enums import Verbosity
from ralph.config.general_config import GeneralConfig
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.recovery import controller as recovery_controller_module
from ralph.recovery.canary_probe import CANARY_PROMPT_FILENAME
from ralph.recovery.connectivity import ConnectivityState
from ralph.recovery.events import FailureEvent, FalloverEvent
from ralph.recovery.testing import FakeConnectivityMonitor
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.invoke import InvokeOptions
    from ralph.pipeline.factory import PipelineDeps

_CYCLE_CAP = 3


def _probe_reachable_deps(
    probed: list[str],
    bundle: PolicyBundle,
) -> PipelineDeps:
    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
        _clock: object = None,
    ) -> Iterator[str]:
        del options, _clock
        if Path(prompt_file).name == CANARY_PROMPT_FILENAME:
            probed.append(config.cmd)
            return iter(("scripted canary reply",))
        raise AgentInvocationError(
            config.cmd,
            0,
            "agent idle timeout",
        )

    deps = make_test_pipeline_deps(
        make_display_context(),
        policy_bundle=bundle,
    )
    return replace(
        deps,
        invoke_agent=fake_invoke_agent,
        recovery_sleep=lambda _: None,
    )


def _test_config() -> UnifiedConfig:
    return UnifiedConfig(
        general=GeneralConfig(
            agent_max_consecutive_unproductive=1,
            agent_max_total_unproductive=1,
        )
    )


def _make_policy_bundle() -> PolicyBundle:
    agents = AgentsPolicy(
        agent_chains={
            "dev-chain": AgentChainConfig(
                agents=["claude", "opencode"],
                retry_delay_ms=0,
            )
        },
        agent_drains={"development": AgentDrainConfig(chain="dev-chain")},
    )
    pipeline = PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete"),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )
    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={}))


def _common_monkeypatches(
    monkeypatch: pytest.MonkeyPatch,
    workspace_root: Path,
    bundle: PolicyBundle,
    fake_execute: object,
    save_fn: object = None,
) -> None:
    monkeypatch.setattr(
        runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(workspace_root)
    )
    monkeypatch.setattr(runner_module, "write_start_commit_if_absent", lambda _: None)
    monkeypatch.setattr(runner_module, "validate_custom_mcp_servers", lambda _: 0)
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _: bundle)
    monkeypatch.setattr(
        runner_module,
        "AgentRegistry",
        MagicMock(from_config=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr(runner_module, "materialize_agent_prompt_if_needed", lambda *a, **kw: None)
    monkeypatch.setattr(runner_module, "materialize_prepared_prompt", lambda *a, **kw: None)
    monkeypatch.setattr(runner_module.ckpt, "save", save_fn if save_fn is not None else MagicMock())
    monkeypatch.setattr(runner_module, "execute_effect_with_optional_display", fake_execute)


def _initial_state() -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode"], current_index=0, retries=0
            )
        },
        policy_entry_phase="development",
        recovery_cycle_cap=_CYCLE_CAP,
    )


def _failing_execute() -> object:
    def _fake_execute(*args: object, **kwargs: object) -> None:
        raise AgentInvocationError(
            "claude",
            0,
            "agent idle timeout",
        )

    return _fake_execute


def test_runner_tries_the_whole_chain_before_stopping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probed: list[str] = []
    bundle = _make_policy_bundle()

    saved_states: list[PipelineState] = []

    def _capture_saved_state(state: PipelineState, path: object = None) -> None:
        saved_states.append(state)

    invocation_count = 0

    def _fake_execute(*args: object, **kwargs: object) -> None:
        nonlocal invocation_count
        invocation_count += 1
        raise AgentInvocationError(
            "claude",
            0,
            "agent idle timeout",
        )

    _common_monkeypatches(monkeypatch, tmp_path, bundle, _fake_execute, _capture_saved_state)

    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)

    exit_code = runner_module.run(
        _test_config(),
        initial_state=_initial_state(),
        verbosity=Verbosity.QUIET,
        connectivity_monitor=monitor,
        pipeline_deps=_probe_reachable_deps(probed, bundle),
    )

    assert exit_code == 1
    assert invocation_count == 2, (
        f"Expected both agents of the chain to run, got {invocation_count} invocations"
    )
    assert probed, "the failed chain attempt must still reach the recovery probe boundary"


@pytest.mark.timeout_seconds(5)
def test_runner_records_failure_and_fallover_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probed: list[str] = []
    bundle = _make_policy_bundle()

    captured_failure_events: list[FailureEvent] = []
    captured_fallover_events: list[FalloverEvent] = []

    class _CapturingBus(recovery_controller_module.FailureEventBus):
        def __init__(self) -> None:
            super().__init__()
            self.subscribe(
                lambda evt: (
                    captured_failure_events.append(evt)
                    if isinstance(evt, FailureEvent)
                    else (
                        captured_fallover_events.append(evt)
                        if isinstance(evt, FalloverEvent)
                        else None
                    )
                )
            )

    monkeypatch.setattr(recovery_controller_module, "FailureEventBus", _CapturingBus)

    _common_monkeypatches(monkeypatch, tmp_path, bundle, _failing_execute())

    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)

    exit_code = runner_module.run(
        _test_config(),
        initial_state=_initial_state(),
        verbosity=Verbosity.QUIET,
        connectivity_monitor=monitor,
        pipeline_deps=_probe_reachable_deps(probed, bundle),
    )

    assert exit_code == 1
    assert len(captured_failure_events) == 2, (
        f"Expected 2 FailureEvents (one per agent), got {len(captured_failure_events)}"
    )
    for evt in captured_failure_events:
        assert evt.category == "agent"
        assert evt.counted_against_budget is True
        assert evt.phase == "development"
        assert evt.agent in ("claude", "opencode")

    assert len(captured_fallover_events) == 1, (
        f"Expected 1 FalloverEvent, got {len(captured_fallover_events)}"
    )
    for evt in captured_fallover_events:
        assert evt.from_agent == "claude"
        assert evt.to_agent == "opencode"
        assert evt.phase == "development"
    assert probed, "the failed chain attempt must still reach the recovery probe boundary"


def test_runner_fallover_history_reflects_agent_transitions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probed: list[str] = []
    bundle = _make_policy_bundle()

    saved_states: list[PipelineState] = []

    def _capture_saved_state(state: PipelineState, path: object = None) -> None:
        saved_states.append(state)

    _common_monkeypatches(monkeypatch, tmp_path, bundle, _failing_execute(), _capture_saved_state)

    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)

    exit_code = runner_module.run(
        _test_config(),
        initial_state=_initial_state(),
        verbosity=Verbosity.QUIET,
        connectivity_monitor=monitor,
        pipeline_deps=_probe_reachable_deps(probed, bundle),
    )

    assert exit_code == 1
    assert len(saved_states) > 0, "Expected at least one saved checkpoint"

    final_state = None
    for state in reversed(saved_states):
        if isinstance(state, PipelineState) and state.recovery_cycle_count > 0:
            final_state = state
            break

    assert final_state is not None, "No state found with recovery_cycle_count > 0"
    assert len(final_state.fallover_history) == 1, (
        f"Expected 1 fallover record, got {len(final_state.fallover_history)}"
    )
    for record in final_state.fallover_history:
        assert record.from_agent == "claude"
        assert record.to_agent == "opencode"
        assert record.phase == "development"
    assert probed, "the failed chain attempt must still reach the recovery probe boundary"


@pytest.mark.timeout_seconds(5)
def test_runner_recovery_cycle_count_advances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probed: list[str] = []
    bundle = _make_policy_bundle()

    saved_states: list[PipelineState] = []

    def _capture_saved_state(state: PipelineState, path: object = None) -> None:
        saved_states.append(state)

    _common_monkeypatches(monkeypatch, tmp_path, bundle, _failing_execute(), _capture_saved_state)

    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)

    exit_code = runner_module.run(
        _test_config(),
        initial_state=_initial_state(),
        verbosity=Verbosity.QUIET,
        connectivity_monitor=monitor,
        pipeline_deps=_probe_reachable_deps(probed, bundle),
    )

    assert exit_code == 1
    cycle_counts = [s.recovery_cycle_count for s in saved_states if isinstance(s, PipelineState)]
    assert max(cycle_counts, default=0) >= 1, (
        f"Expected recovery_cycle_count to reach at least 1 in saved states; "
        f"got max={max(cycle_counts, default=0)}"
    )
    assert probed, "the failed chain attempt must still reach the recovery probe boundary"
