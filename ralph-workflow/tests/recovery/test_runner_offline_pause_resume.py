from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import ralph.recovery.controller as recovery_controller_module
from ralph.config.enums import Verbosity
from ralph.pipeline import run_loop as run_loop_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.integration_resolution import RESOLVED
from ralph.pipeline.integration_resolution_types import IntegrationResolutionVerdict
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
from ralph.recovery.connectivity import ConnectivityState
from ralph.recovery.events import FailureEvent, FailureEventBus
from ralph.recovery.testing import FakeConnectivityMonitor
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest


def _make_policy_bundle() -> PolicyBundle:
    agents = AgentsPolicy(
        agent_chains={
            "dev-chain": AgentChainConfig(
                agents=["claude"],
                max_retries=3,
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
            "complete": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="complete"),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )
    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={}))


def _make_initial_state() -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(agents=["claude"], current_index=0, retries=0)
        },
        policy_entry_phase="development",
        recovery_cycle_cap=10,
    )


def _patch_runner_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tmp_git_repo: Path,
    bundle: PolicyBundle,
    execute: Callable[..., PipelineEvent],
) -> None:
    monkeypatch.setattr(
        runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_git_repo)
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
    monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
    monkeypatch.setattr(runner_module, "execute_effect_with_optional_display", execute)
    monkeypatch.setattr(
        runner_module,
        "phase_event_after_agent_run",
        lambda **kwargs: PipelineEvent.AGENT_SUCCESS,
    )
    monkeypatch.setattr(runner_module, "auto_integrate_on_phase_transition", lambda *a, **kw: None)
    monkeypatch.setattr(run_loop_module, "_run_startup_integration", lambda *a, **kw: None)
    resolved = IntegrationResolutionVerdict(RESOLVED)
    monkeypatch.setattr(
        runner_module, "inspect_integration_resolution", lambda *_, write_back=None: resolved
    )
    monkeypatch.setattr(
        run_loop_module, "inspect_integration_resolution", lambda *_, write_back=None: resolved
    )


def _monitor_that_reconnects_on_pause(
    on_pause: Callable[[], None],
) -> FakeConnectivityMonitor:
    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.OFFLINE)
    original_add_listener = monitor.add_listener
    paused = False

    def _intercepted_add_listener(cb: object) -> object:
        nonlocal paused
        unsub = original_add_listener(cb)
        if not paused:
            paused = True
            on_pause()
            monitor.go_online("test connectivity restored")
        return unsub

    monitor.add_listener = _intercepted_add_listener
    return monitor


def test_offline_pauses_agent_invocation_and_resume_completes(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation_count = 0

    def _fake_execute(*args: object, **kwargs: object) -> PipelineEvent:
        nonlocal invocation_count
        invocation_count += 1
        return PipelineEvent.AGENT_SUCCESS

    _patch_runner_seams(
        monkeypatch,
        tmp_git_repo=tmp_git_repo,
        bundle=_make_policy_bundle(),
        execute=_fake_execute,
    )

    invocations_at_pause: list[int] = []
    monitor = _monitor_that_reconnects_on_pause(
        lambda: invocations_at_pause.append(invocation_count)
    )

    exit_code = runner_module.run(
        MagicMock(),
        initial_state=_make_initial_state(),
        verbosity=Verbosity.QUIET,
        connectivity_monitor=monitor,
        _recovery_sleep=lambda _: None,
    )

    assert invocations_at_pause == [0], (
        "Runner must reach the offline check exactly once and invoke no agent "
        f"while the monitor is OFFLINE; saw {invocations_at_pause}"
    )
    assert exit_code == 0, f"Runner exited with code {exit_code} instead of 0 after coming online"
    assert invocation_count == 1, (
        f"Expected 1 agent invocation after going online, got {invocation_count}"
    )
    assert monitor.current_state is ConnectivityState.ONLINE


def test_offline_window_produces_no_failure_events(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_failure_events: list[FailureEvent] = []

    class _CapturingBus(FailureEventBus):
        def __init__(self) -> None:
            super().__init__()
            self.subscribe(
                lambda evt: (
                    captured_failure_events.append(evt) if isinstance(evt, FailureEvent) else None
                )
            )

    monkeypatch.setattr(recovery_controller_module, "FailureEventBus", _CapturingBus)

    _patch_runner_seams(
        monkeypatch,
        tmp_git_repo=tmp_git_repo,
        bundle=_make_policy_bundle(),
        execute=lambda *args, **kwargs: PipelineEvent.AGENT_SUCCESS,
    )

    events_at_pause: list[int] = []
    monitor = _monitor_that_reconnects_on_pause(
        lambda: events_at_pause.append(len(captured_failure_events))
    )

    exit_code = runner_module.run(
        MagicMock(),
        initial_state=_make_initial_state(),
        verbosity=Verbosity.QUIET,
        connectivity_monitor=monitor,
        _recovery_sleep=lambda _: None,
    )

    assert events_at_pause == [0], (
        f"Expected no FailureEvents during the offline window, saw {events_at_pause}"
    )
    assert captured_failure_events == [], (
        f"Expected no FailureEvents during offline window, "
        f"got {len(captured_failure_events)}: {captured_failure_events}"
    )
    assert exit_code == 0
