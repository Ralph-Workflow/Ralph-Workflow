"""Runner-level integration test: phase scope kills phase-labeled processes on exit.

This test drives the real runner path — runner_module.run() — to verify
that process_phase_scope (at the _run_pipeline_step boundary) cleans up
all phase-labeled child processes when the phase finishes.
"""

from __future__ import annotations

import contextlib
import itertools
import sys
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from pathlib import Path

import pytest

import ralph.process.manager as _mgr
from ralph.config.enums import Verbosity
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.process.manager import (
    ProcessManager,
    ProcessManagerPolicy,
    ProcessStatus,
    get_process_manager,
    reset_process_manager,
)
from ralph.testing.fake_process import make_sync_process_factory
from ralph.workspace.scope import WorkspaceScope

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3, kill_followup_timeout_s=0.5, log_events=False
)

_INTERRUPT_EXIT_CODE = 130
PYTHON = sys.executable
_TEST_PHASE = "fake-phase"


@pytest.fixture(autouse=True)
def _reset_pm() -> None:
    reset_process_manager()
    yield
    with contextlib.suppress(Exception):
        get_process_manager().shutdown_all(grace_period_s=0)
    reset_process_manager()


def _stub_determine_effect(effects: list[object]) -> object:
    def stub(
        state: object,
        policy_bundle: object,
        workspace_scope: object = None,
        *,
        config: object = None,
    ) -> object:
        return effects.pop(0)

    return stub


def _apply_runner_stubs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    effects: list[object],
    *,
    fake_execute_agent_effect: object,
) -> None:
    complete_state = PipelineState(phase="complete")
    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(runner_module, "_write_start_commit_if_absent", lambda _: None)
    monkeypatch.setattr(runner_module, "_validate_custom_mcp_servers", lambda _: 0)
    _mock_bundle = MagicMock()
    _mock_bundle.pipeline.terminal_phase = "complete"
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda *a, **kw: _mock_bundle)
    monkeypatch.setattr(runner_module, "AgentRegistry", MagicMock())
    monkeypatch.setattr(
        runner_module, "_determine_effect_from_policy", _stub_determine_effect(effects)
    )
    monkeypatch.setattr(runner_module, "_execute_agent_effect", fake_execute_agent_effect)
    monkeypatch.setattr(runner_module, "_materialize_agent_prompt_if_needed", lambda *a, **kw: None)
    monkeypatch.setattr(
        runner_module, "_phase_event_after_agent_run", lambda **kw: PipelineEvent.AGENT_SUCCESS
    )
    monkeypatch.setattr(runner_module, "reducer_reduce", lambda *a, **kw: (complete_state, []))
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _: None)


def test_runner_phase_scope_kills_phase_labeled_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_run_pipeline_step wraps phase body in process_phase_scope(state.phase).

    A process spawned inside _execute_agent_effect with label 'phase:<phase>:worker'
    must be dead after runner.run() exits, because the phase scope tears it down.
    """
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(policy=_FAST_POLICY, sync_process_factory=sync_factory)
    spawned_pid: list[int] = []

    def fake_execute_agent_effect(
        effect: object, config: object, deps: object, workspace_scope: object, **kwargs: object
    ) -> PipelineEvent:
        handle = get_process_manager().spawn(
            [PYTHON, "-c", "pass"],
            label=f"phase:{_TEST_PHASE}:worker",
        )
        spawned_pid.append(handle.record.pid)
        return PipelineEvent.AGENT_SUCCESS

    effects: list[object] = [
        InvokeAgentEffect(
            agent_name="fake-agent",
            phase=_TEST_PHASE,
            prompt_file="/dev/null",
        ),
    ]
    _apply_runner_stubs(
        monkeypatch, tmp_path, effects, fake_execute_agent_effect=fake_execute_agent_effect
    )

    initial_state = MagicMock()
    initial_state.phase = _TEST_PHASE
    initial_state.recovery_epoch = 0

    original_singleton = _mgr._singleton
    _mgr._singleton = pm
    try:
        exit_code = runner_module.run(
            MagicMock(), initial_state=initial_state, verbosity=Verbosity.QUIET
        )
    finally:
        _mgr._singleton = original_singleton

    assert exit_code == 0
    assert spawned_pid, "Fake handler must have spawned a process"

    # Verify the process was terminated by the phase scope
    record = pm._records.get(spawned_pid[0])
    assert record is not None
    assert record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED)


def test_runner_phase_scope_does_not_kill_other_labels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """process_phase_scope only kills processes whose label starts with 'phase:<phase_name>'."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(policy=_FAST_POLICY, sync_process_factory=sync_factory)
    spawned: dict[str, int] = {}

    def fake_execute_agent_effect(
        effect: object, config: object, deps: object, workspace_scope: object, **kwargs: object
    ) -> PipelineEvent:
        phase_handle = get_process_manager().spawn(
            [PYTHON, "-c", "pass"],
            label=f"phase:{_TEST_PHASE}:worker",
        )
        bystander_handle = get_process_manager().spawn(
            [PYTHON, "-c", "pass"],
            label="other:unrelated",
        )
        spawned["phase"] = phase_handle.record.pid
        spawned["bystander"] = bystander_handle.record.pid
        return PipelineEvent.AGENT_SUCCESS

    effects: list[object] = [
        InvokeAgentEffect(
            agent_name="fake-agent",
            phase=_TEST_PHASE,
            prompt_file="/dev/null",
        ),
    ]
    _apply_runner_stubs(
        monkeypatch, tmp_path, effects, fake_execute_agent_effect=fake_execute_agent_effect
    )

    initial_state = MagicMock()
    initial_state.phase = _TEST_PHASE
    initial_state.recovery_epoch = 0

    original_singleton = _mgr._singleton
    _mgr._singleton = pm
    try:
        exit_code = runner_module.run(
            MagicMock(), initial_state=initial_state, verbosity=Verbosity.QUIET
        )
    finally:
        _mgr._singleton = original_singleton

    assert exit_code == 0
    assert spawned, "Fake handler must have spawned processes"

    # Phase-labeled process should be killed
    phase_record = pm._records.get(spawned["phase"])
    assert phase_record is not None
    assert phase_record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED)

    # Bystander should still be running (then we clean it up)
    bystander_record = pm._records.get(spawned["bystander"])
    assert bystander_record is not None
    assert bystander_record.status == ProcessStatus.RUNNING


def test_runner_interrupt_shuts_down_tracked_children_even_outside_phase_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """KeyboardInterrupt must tear down tracked children before returning 130.

    This covers the serial runner path, where agent subprocesses are not labeled
    under ``phase:<phase>`` and therefore cannot rely on ``process_phase_scope``
    prefix cleanup alone.
    """
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(policy=_FAST_POLICY, sync_process_factory=sync_factory)
    spawned_pid: list[int] = []

    def fake_execute_agent_effect(
        effect: object, config: object, deps: object, workspace_scope: object, **kwargs: object
    ) -> PipelineEvent:
        handle = get_process_manager().spawn(
            [PYTHON, "-c", "pass"],
            label="invoke:fake-agent",
        )
        spawned_pid.append(handle.record.pid)
        raise KeyboardInterrupt

    effects: list[object] = [
        InvokeAgentEffect(
            agent_name="fake-agent",
            phase=_TEST_PHASE,
            prompt_file="/dev/null",
        ),
    ]
    _apply_runner_stubs(
        monkeypatch, tmp_path, effects, fake_execute_agent_effect=fake_execute_agent_effect
    )

    initial_state = MagicMock()
    initial_state.phase = _TEST_PHASE
    initial_state.recovery_epoch = 0
    interrupted_state = MagicMock()
    initial_state.copy_with.return_value = interrupted_state
    saved_states: list[object] = []
    monkeypatch.setattr(runner_module.ckpt, "save", saved_states.append)

    original_singleton = _mgr._singleton
    _mgr._singleton = pm
    try:
        exit_code = runner_module.run(
            MagicMock(), initial_state=initial_state, verbosity=Verbosity.QUIET
        )
    finally:
        _mgr._singleton = original_singleton

    assert exit_code == _INTERRUPT_EXIT_CODE
    assert spawned_pid, "Fake handler must have spawned a tracked child"

    # Verify the process was terminated
    record = pm._records.get(spawned_pid[0])
    assert record is not None
    assert record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED)

    initial_state.copy_with.assert_called_once_with(interrupted_by_user=True)
    assert saved_states == [interrupted_state]
