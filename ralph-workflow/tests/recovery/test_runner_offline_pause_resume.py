"""Black-box end-to-end test: runner pauses when offline and resumes when online.

While the connectivity monitor reports OFFLINE, the runner must not invoke any
agent and must not debit any budget. When connectivity is restored, the runner
must resume and complete normally with no false-positive FailureEvents.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from ralph.config.enums import Verbosity
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
from ralph.recovery.connectivity import ConnectivityState
from ralph.recovery.events import FailureEvent
from ralph.recovery.testing import FakeConnectivityMonitor
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_ONLINE_WAIT_TIMEOUT_S = 10.0


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
        },
        entry_phase="development",
        terminal_phase="complete",
    )
    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={}))


def test_offline_pauses_agent_invocation_and_resume_completes(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runner pauses while OFFLINE and auto-resumes when connectivity returns.

    Proof of behavior:
    1. Runner starts with monitor OFFLINE → blocks in _apply_connectivity_check.
    2. No agent is invoked during the offline window (invocation_count == 0).
    3. Calling go_online() unblocks the runner.
    4. Runner completes successfully (exit code 0).
    5. No FailureEvents are emitted during the offline window.
    """
    from ralph.pipeline.events import PipelineEvent  # noqa: PLC0415

    bundle = _make_policy_bundle()

    initial_state = PipelineState(
        phase="development",
        dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=0),
        policy_entry_phase="development",
        recovery_cycle_cap=10,
    )

    invocation_count = 0

    def _fake_execute(*args: Any, **kwargs: Any) -> PipelineEvent:
        nonlocal invocation_count
        invocation_count += 1
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_git_repo)
    )
    monkeypatch.setattr(runner_module, "_write_start_commit_if_absent", lambda _: None)
    monkeypatch.setattr(runner_module, "_validate_custom_mcp_servers", lambda _: 0)
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _: bundle)
    monkeypatch.setattr(
        runner_module,
        "AgentRegistry",
        MagicMock(from_config=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr(
        runner_module, "_materialize_agent_prompt_if_needed", lambda *a, **kw: None
    )
    monkeypatch.setattr(runner_module, "_materialize_prepared_prompt", lambda *a, **kw: None)
    monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
    monkeypatch.setattr(runner_module, "_execute_effect_with_optional_display", _fake_execute)
    monkeypatch.setattr(
        runner_module,
        "_phase_event_after_agent_run",
        lambda **kwargs: PipelineEvent.AGENT_SUCCESS,
    )

    # Monitor starts OFFLINE so the runner will block on first loop iteration
    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.OFFLINE)

    # Signal set when the runner thread has started blocking (listener was registered)
    listener_registered = threading.Event()
    original_add_listener = monitor.add_listener

    def _intercepted_add_listener(cb: Any) -> Any:
        unsub = original_add_listener(cb)
        listener_registered.set()
        return unsub

    monitor.add_listener = _intercepted_add_listener  # type: ignore[method-assign]

    runner_result: list[int] = []
    runner_exc: list[BaseException] = []

    def _run_in_thread() -> None:
        try:
            rc = runner_module.run(
                MagicMock(),
                initial_state=initial_state,
                verbosity=Verbosity.QUIET,
                connectivity_monitor=monitor,
                _recovery_sleep=lambda _: None,
            )
            runner_result.append(rc)
        except BaseException as exc:
            runner_exc.append(exc)

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()

    # Wait until the runner has registered its listener (it is now blocking)
    assert listener_registered.wait(timeout=_ONLINE_WAIT_TIMEOUT_S), (
        "Runner never registered a connectivity listener — it did not reach the offline check"
    )

    # At this point the runner is blocked in _apply_connectivity_check.
    # No agent invocation should have occurred.
    assert invocation_count == 0, (
        f"Agent was invoked {invocation_count} time(s) while monitor was OFFLINE"
    )

    # Restore connectivity — the runner should unblock and complete
    monitor.go_online("test connectivity restored")

    thread.join(timeout=_ONLINE_WAIT_TIMEOUT_S)
    assert not thread.is_alive(), "Runner thread did not complete after going online"

    if runner_exc:
        raise runner_exc[0]

    assert runner_result == [0], (
        f"Runner exited with code {runner_result} instead of 0 after coming online"
    )
    # Agent was invoked exactly once after coming online
    assert invocation_count == 1, (
        f"Expected 1 agent invocation after going online, got {invocation_count}"
    )


def test_offline_window_produces_no_failure_events(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No FailureEvents are emitted while the monitor is OFFLINE.

    The offline period must be completely silent — no budget debits,
    no failure events, no fallover records.
    """
    from ralph.pipeline.events import PipelineEvent  # noqa: PLC0415
    from ralph.recovery import events as recovery_events_module  # noqa: PLC0415

    bundle = _make_policy_bundle()

    initial_state = PipelineState(
        phase="development",
        dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=0),
        policy_entry_phase="development",
        recovery_cycle_cap=10,
    )

    captured_failure_events: list[FailureEvent] = []

    class _CapturingBus(recovery_events_module.FailureEventBus):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            self.subscribe(
                lambda evt: (
                    captured_failure_events.append(evt)
                    if isinstance(evt, FailureEvent)
                    else None
                )
            )

    monkeypatch.setattr(recovery_events_module, "FailureEventBus", _CapturingBus)

    def _fake_execute(*args: Any, **kwargs: Any) -> PipelineEvent:
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_git_repo)
    )
    monkeypatch.setattr(runner_module, "_write_start_commit_if_absent", lambda _: None)
    monkeypatch.setattr(runner_module, "_validate_custom_mcp_servers", lambda _: 0)
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _: bundle)
    monkeypatch.setattr(
        runner_module,
        "AgentRegistry",
        MagicMock(from_config=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr(
        runner_module, "_materialize_agent_prompt_if_needed", lambda *a, **kw: None
    )
    monkeypatch.setattr(runner_module, "_materialize_prepared_prompt", lambda *a, **kw: None)
    monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
    monkeypatch.setattr(runner_module, "_execute_effect_with_optional_display", _fake_execute)
    monkeypatch.setattr(
        runner_module,
        "_phase_event_after_agent_run",
        lambda **kwargs: PipelineEvent.AGENT_SUCCESS,
    )

    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.OFFLINE)

    listener_registered = threading.Event()
    original_add_listener = monitor.add_listener

    def _intercepted_add_listener(cb: Any) -> Any:
        unsub = original_add_listener(cb)
        listener_registered.set()
        return unsub

    monitor.add_listener = _intercepted_add_listener  # type: ignore[method-assign]

    runner_result: list[int] = []

    def _run_in_thread() -> None:
        rc = runner_module.run(
            MagicMock(),
            initial_state=initial_state,
            verbosity=Verbosity.QUIET,
            connectivity_monitor=monitor,
            _recovery_sleep=lambda _: None,
        )
        runner_result.append(rc)

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()

    assert listener_registered.wait(timeout=_ONLINE_WAIT_TIMEOUT_S)

    # No failure events while offline
    offline_event_count = len(captured_failure_events)

    monitor.go_online("test restored")
    thread.join(timeout=_ONLINE_WAIT_TIMEOUT_S)

    # No failure events should have been emitted at all (offline + online succeeded)
    assert captured_failure_events == [], (
        f"Expected no FailureEvents during offline window, "
        f"got {len(captured_failure_events)}: {captured_failure_events}"
    )
    assert offline_event_count == 0
    assert runner_result == [0]
