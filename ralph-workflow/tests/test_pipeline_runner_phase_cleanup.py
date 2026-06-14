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
from ralph.agents.invoke import AgentInvocationError
from ralph.config.enums import Verbosity
from ralph.config.models import AgentConfig
from ralph.display.context import make_display_context
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.process.manager import (
    ProcessManager,
    ProcessManagerPolicy,
    ProcessStatus,
    SpawnOptions,
    get_process_manager,
    reset_process_manager,
)
from ralph.testing.fake_process import FakePsutil, make_sync_process_factory
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3,
    kill_followup_timeout_s=0.5,
    log_events=False,
    enable_zombie_reaper=False,
)

_INTERRUPT_EXIT_CODE = 130
PYTHON = sys.executable
_TEST_PHASE = "fake-phase"


def _config() -> object:
    config = MagicMock()
    config.general.verbosity = 0
    return config


class _FakeBridge:
    """Minimal stand-in for an MCP session bridge."""

    def shutdown(self) -> None:
        pass

    def agent_endpoint_uri(self) -> str:
        return "http://127.0.0.1:12345/mcp"

    def reset_tool_registry(self) -> None:
        pass


def _fake_registry_factory(config: object) -> object:
    class _Registry:
        def get(self, name: str) -> AgentConfig | None:
            if name == "fake-agent":
                return AgentConfig(cmd="fake")
            return None

    return _Registry()


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
    fake_invoke_agent: object,
) -> object:
    complete_state = PipelineState(phase="complete")
    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(runner_module, "write_start_commit_if_absent", lambda _: None)
    monkeypatch.setattr(runner_module, "validate_custom_mcp_servers", lambda _: 0)
    _mock_bundle = MagicMock()
    _mock_bundle.pipeline.terminal_phase = "complete"
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda *a, **kw: _mock_bundle)
    monkeypatch.setattr(
        runner_module, "determine_effect_from_policy", _stub_determine_effect(effects)
    )
    monkeypatch.setattr(runner_module, "materialize_agent_prompt_if_needed", lambda *a, **kw: None)
    monkeypatch.setattr(
        runner_module, "phase_event_after_agent_run", lambda **kw: PipelineEvent.AGENT_SUCCESS
    )
    monkeypatch.setattr(runner_module, "reducer_reduce", lambda *a, **kw: (complete_state, []))
    monkeypatch.setattr(runner_module.ckpt, "save", lambda *_args, **_kwargs: None)

    pipeline_deps = make_test_pipeline_deps(
        make_display_context(),
        bridge=_FakeBridge(),
        registry_factory=_fake_registry_factory,
        policy_bundle=_mock_bundle,
    )

    def _execute_agent_effect_wrapper(
        effect: object, config: object, deps: object, workspace_scope: object, **kwargs: object
    ) -> PipelineEvent:
        return effect_executor_module.execute_agent_effect(
            effect,
            config,
            deps,
            workspace_scope,
            bridge=_FakeBridge(),
            invoke_agent=fake_invoke_agent,
            agent_invocation_error=AgentInvocationError,
            **kwargs,
        )

    monkeypatch.setattr(runner_module, "execute_agent_effect", _execute_agent_effect_wrapper)
    return pipeline_deps


def test_runner_phase_scope_kills_phase_labeled_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_run_pipeline_step wraps phase body in process_phase_scope(state.phase).

    A process spawned inside _execute_agent_effect with label 'phase:<phase>:worker'
    must be dead after runner.run() exits, because the phase scope tears it down.
    """
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(policy=_FAST_POLICY, sync_process_factory=sync_factory, psutil=FakePsutil())
    spawned_pid: list[int] = []

    def fake_invoke_agent(
        agent_config: object, prompt_file: str, *, options: object = None
    ) -> object:
        handle = get_process_manager().spawn(
            [PYTHON, "-c", "pass"],
            SpawnOptions(label=f"phase:{_TEST_PHASE}:worker"),
        )
        spawned_pid.append(handle.record.pid)
        return iter(["line"])

    effects: list[object] = [
        InvokeAgentEffect(
            agent_name="fake-agent",
            phase=_TEST_PHASE,
            prompt_file="/dev/null",
        ),
    ]
    pipeline_deps = _apply_runner_stubs(
        monkeypatch, tmp_path, effects, fake_invoke_agent=fake_invoke_agent
    )

    initial_state = PipelineState(phase=_TEST_PHASE)

    original_singleton = _mgr._pm_state.instance
    _mgr._pm_state.instance = pm
    try:
        exit_code = runner_module.run(
            _config(),
            initial_state=initial_state,
            verbosity=Verbosity.QUIET,
            pipeline_deps=pipeline_deps,
        )
    finally:
        _mgr._pm_state.instance = original_singleton

    assert exit_code == 0
    assert spawned_pid, "Fake handler must have spawned a process"

    # Verify the process was terminated by the phase scope
    record = pm.get_record(spawned_pid[0])
    assert record is not None
    assert record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED)


def test_runner_phase_scope_does_not_kill_other_labels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """process_phase_scope only kills processes whose label starts with 'phase:<phase_name>'."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(policy=_FAST_POLICY, sync_process_factory=sync_factory, psutil=FakePsutil())
    spawned: dict[str, int] = {}

    def fake_invoke_agent(
        agent_config: object, prompt_file: str, *, options: object = None
    ) -> object:
        phase_handle = get_process_manager().spawn(
            [PYTHON, "-c", "pass"],
            SpawnOptions(label=f"phase:{_TEST_PHASE}:worker"),
        )
        bystander_handle = get_process_manager().spawn(
            [PYTHON, "-c", "pass"],
            SpawnOptions(label="other:unrelated"),
        )
        spawned["phase"] = phase_handle.record.pid
        spawned["bystander"] = bystander_handle.record.pid
        return iter(["line"])

    effects: list[object] = [
        InvokeAgentEffect(
            agent_name="fake-agent",
            phase=_TEST_PHASE,
            prompt_file="/dev/null",
        ),
    ]
    pipeline_deps = _apply_runner_stubs(
        monkeypatch, tmp_path, effects, fake_invoke_agent=fake_invoke_agent
    )

    initial_state = PipelineState(phase=_TEST_PHASE)

    original_singleton = _mgr._pm_state.instance
    _mgr._pm_state.instance = pm
    try:
        exit_code = runner_module.run(
            _config(),
            initial_state=initial_state,
            verbosity=Verbosity.QUIET,
            pipeline_deps=pipeline_deps,
        )
    finally:
        _mgr._pm_state.instance = original_singleton

    assert exit_code == 0
    assert spawned, "Fake handler must have spawned processes"

    # Phase-labeled process should be killed
    phase_record = pm.get_record(spawned["phase"])
    assert phase_record is not None
    assert phase_record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED)

    # Bystander should still be running (then we clean it up)
    bystander_record = pm.get_record(spawned["bystander"], include_terminal=False)
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
    pm = ProcessManager(policy=_FAST_POLICY, sync_process_factory=sync_factory, psutil=FakePsutil())
    spawned_pid: list[int] = []

    def fake_invoke_agent(
        agent_config: object, prompt_file: str, *, options: object = None
    ) -> object:
        handle = get_process_manager().spawn(
            [PYTHON, "-c", "pass"],
            SpawnOptions(label="invoke:fake-agent"),
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
    pipeline_deps = _apply_runner_stubs(
        monkeypatch, tmp_path, effects, fake_invoke_agent=fake_invoke_agent
    )

    initial_state = PipelineState(phase=_TEST_PHASE)
    saved_states: list[PipelineState] = []

    def _save_state(saved_state: PipelineState, *_args: object, **_kwargs: object) -> None:
        saved_states.append(saved_state)

    monkeypatch.setattr(runner_module.ckpt, "save", _save_state)

    original_singleton = _mgr._pm_state.instance
    _mgr._pm_state.instance = pm
    try:
        exit_code = runner_module.run(
            _config(),
            initial_state=initial_state,
            verbosity=Verbosity.QUIET,
            pipeline_deps=pipeline_deps,
        )
    finally:
        _mgr._pm_state.instance = original_singleton

    assert exit_code == _INTERRUPT_EXIT_CODE
    assert spawned_pid, "Fake handler must have spawned a tracked child"

    # Verify the process was terminated
    record = pm.get_record(spawned_pid[0])
    assert record is not None
    assert record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED)

    assert len(saved_states) == 1
    assert saved_states[0].interrupted_by_user is True
    assert saved_states[0].phase == _TEST_PHASE
