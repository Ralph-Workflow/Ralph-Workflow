"""Black-box end-to-end test: runner pauses when offline and resumes when online.

While the connectivity monitor reports OFFLINE, the runner must not invoke any
agent and must not debit any budget. When connectivity is restored, the runner
must resume and complete normally with no false-positive FailureEvents.

Both tests drive the runner on the CALLING thread. ``_apply_connectivity_check``
registers a connectivity listener and then blocks on a
:class:`threading.Event` *only* on the offline path, so listener registration
is itself the observable "the runner has paused" edge, and it is the seam this
module injects on: the fake monitor's ``add_listener`` records the state of the
world at the pause and then restores connectivity, which fires the listener
synchronously and lets the runner continue. That keeps the proof identical --
no agent may have run at the pause, and the run must complete after it -- while
removing the real daemon thread and the ``Event.wait`` handshake the previous
version needed. Those made the test depend on the OS scheduler, which under a
loaded ``pytest -n 4`` run pushed it past the 1.0 s per-test budget even though
nothing about the runner had changed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import ralph.recovery.controller as recovery_controller_module
from ralph.config.enums import Verbosity
from ralph.pipeline import run_loop as run_loop_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.events import PipelineEvent
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
    """Neutralise every seam the offline contract does not depend on."""
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
    # Skip the post-commit auto-integrate step so the runner does not shell
    # out to ``git`` during the test. The seam is identical to the one used
    # by ``tests/test_auto_integrate_parallel_wiring.py`` and
    # ``tests/test_runner_auto_integrate_seam.py``: replacing the function
    # on both ``runner_module`` (used by the per-step hook) and
    # ``run_loop_module`` (used by the startup integration preamble)
    # eliminates the real subprocess + psutil work that the offline tests
    # were paying per run. The offline/online assertions do not depend on
    # auto-integrate behaviour, so neutralising it here keeps the test
    # deterministic within the 1.0 s per-test policy limit without
    # weakening the black-box contract.
    monkeypatch.setattr(runner_module, "auto_integrate_on_phase_transition", lambda *a, **kw: None)
    monkeypatch.setattr(run_loop_module, "auto_integrate_on_phase_transition", lambda *a, **kw: None)


def _monitor_that_reconnects_on_pause(
    on_pause: Callable[[], None],
) -> FakeConnectivityMonitor:
    """An OFFLINE monitor that comes back online the moment the runner pauses.

    ``_apply_connectivity_check`` registers its listener only after it has
    decided the pipeline is offline, so ``add_listener`` is exactly the
    "runner has paused" edge. Restoring connectivity from inside that call
    dispatches the ONLINE event to the just-registered listener
    synchronously, so the runner's wake event is already set when it looks,
    and it never blocks.
    """
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
    """Runner pauses while OFFLINE and auto-resumes when connectivity returns.

    Proof of behavior:
    1. Runner starts with monitor OFFLINE → reaches _apply_connectivity_check
       and registers a connectivity listener (the pause).
    2. No agent has been invoked at that point (invocations_at_pause == 0).
    3. Restoring connectivity unblocks the runner.
    4. Runner completes successfully (exit code 0).
    5. The agent runs exactly once, after connectivity returned.
    """
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
    """No FailureEvents are emitted while the monitor is OFFLINE.

    The offline period must be completely silent — no budget debits,
    no failure events, no fallover records.
    """
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
    # No failure events should have been emitted at all (offline + online succeeded)
    assert captured_failure_events == [], (
        f"Expected no FailureEvents during offline window, "
        f"got {len(captured_failure_events)}: {captured_failure_events}"
    )
    assert exit_code == 0
