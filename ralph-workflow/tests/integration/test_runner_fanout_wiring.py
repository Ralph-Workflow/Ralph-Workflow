"""Integration tests for fan-out wiring in pipeline runner."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

from ralph.config.enums import PHASE_DEVELOPMENT, PHASE_PLANNING
from ralph.config.models import UnifiedConfig
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import FanOutDevelopmentEffect, InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus
from ralph.workspace.scope import WorkspaceScope


def _make_work_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(unit_id=unit_id, description=f"Work unit {unit_id}")


def _make_policy_bundle(max_workers: int = 4) -> MagicMock:
    bundle = MagicMock()
    bundle.pipeline.phases = {
        PHASE_DEVELOPMENT: MagicMock(requires_commit=False, drain="development"),
        PHASE_PLANNING: MagicMock(requires_commit=False, drain="planning"),
    }
    bundle.pipeline.parallel_execution.max_parallel_workers = max_workers
    bundle.agents.agent_drains = {
        "development": MagicMock(chain="developer"),
        "planning": MagicMock(chain="planner"),
    }
    bundle.agents.agent_chains = {
        "developer": MagicMock(agents=["developer"]),
        "planner": MagicMock(agents=["planner"]),
    }
    return bundle


class TestFanOutRouting:
    """Test that runner routes correctly based on work_units."""

    def test_serial_when_no_work_units(self) -> None:
        """When work_units=(), development phase uses InvokeAgentEffect (serial path)."""
        state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=())
        policy_bundle = _make_policy_bundle()

        effect = runner_module._determine_effect_from_policy(
            state,
            policy_bundle,
            config=UnifiedConfig(),
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.phase == PHASE_DEVELOPMENT

    def test_fanout_when_work_units_present(self) -> None:
        """When work_units present, development phase uses FanOutDevelopmentEffect."""
        units = (
            _make_work_unit("unit-a"),
            _make_work_unit("unit-b"),
        )
        max_workers = 3
        state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=units)
        policy_bundle = _make_policy_bundle(max_workers=max_workers)

        effect = runner_module._determine_effect_from_policy(
            state,
            policy_bundle,
            config=UnifiedConfig(),
        )

        assert isinstance(effect, FanOutDevelopmentEffect)
        assert effect.work_units == units
        assert effect.max_workers == max_workers

    def test_non_development_phase_not_affected(self) -> None:
        """Other phases always use InvokeAgentEffect regardless of work_units."""
        units = (_make_work_unit("unit-a"),)
        state = PipelineState(phase=PHASE_PLANNING, work_units=units)
        policy_bundle = _make_policy_bundle()

        effect = runner_module._determine_effect_from_policy(
            state,
            policy_bundle,
            config=UnifiedConfig(),
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.phase == PHASE_PLANNING


def test_execute_fan_out_sync_wires_signal_handlers_and_isolation(monkeypatch, tmp_path) -> None:
    unit = _make_work_unit("unit-a")
    effect = FanOutDevelopmentEffect(work_units=(unit,), max_workers=1)
    state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=(unit,))
    policy_bundle = _make_policy_bundle(max_workers=1)
    workspace_scope = WorkspaceScope(tmp_path)
    install_calls: list[tuple[object, object, object]] = []
    coordinator_calls: list[dict[str, object]] = []
    executor_calls: list[dict[str, object]] = []
    worktree_manager_calls: list[dict[str, object]] = []
    mcp_factory_calls: list[dict[str, object]] = []

    class _FakeExecutor:
        def __init__(self, command, signal_bridge=None) -> None:
            executor_calls.append({"command": tuple(command), "signal_bridge": signal_bridge})

    class _FakeWorktreeManager:
        def __init__(self, repo_root, git) -> None:
            worktree_manager_calls.append({"repo_root": repo_root, "git": git})

    class _FakeMcpFactory:
        def __init__(self, workspace) -> None:
            mcp_factory_calls.append({"workspace": workspace})

    def _fake_install(loop, root_task, bridge) -> None:
        install_calls.append((loop, root_task, bridge))

    async def _fake_run_fan_out(**kwargs):
        coordinator_calls.append(kwargs)
        return []

    async def _fake_integrate(**kwargs):
        return SimpleNamespace(events=[])

    monkeypatch.setattr("ralph.interrupt.asyncio_bridge.install_signal_handlers", _fake_install)
    monkeypatch.setattr("ralph.agents.subprocess_executor.SubprocessAgentExecutor", _FakeExecutor)
    monkeypatch.setattr("ralph.git.worktree_manager.WorktreeManager", _FakeWorktreeManager)
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
    )
    monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
    monkeypatch.setattr("ralph.pipeline.parallel.merge_integrator.integrate", _fake_integrate)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

    runner_module._execute_fan_out_sync(
        effect=effect,
        state=state,
        display=runner_module._LegacyConsoleDisplay(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    assert len(install_calls) == 1
    assert len(executor_calls) == 1
    assert executor_calls[0]["signal_bridge"] is install_calls[0][2]
    assert worktree_manager_calls[0]["repo_root"] == tmp_path.resolve()
    assert mcp_factory_calls
    ctx = cast("Any", coordinator_calls[0]["ctx"])
    assert ctx.isolation is not None


def test_execute_fan_out_sync_converts_unexpected_coordinator_error_to_failed_recovery_state(
    monkeypatch, tmp_path
) -> None:
    unit = _make_work_unit("unit-a")
    effect = FanOutDevelopmentEffect(work_units=(unit,), max_workers=1)
    state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=(unit,))
    policy_bundle = _make_policy_bundle(max_workers=1)
    workspace_scope = WorkspaceScope(tmp_path)

    class _FakeExecutor:
        def __init__(self, command, signal_bridge=None) -> None:
            del command, signal_bridge

    class _FakeWorktreeManager:
        def __init__(self, repo_root, git) -> None:
            del repo_root, git

    class _FakeMcpFactory:
        def __init__(self, workspace) -> None:
            del workspace

    async def _boom(**kwargs):
        del kwargs
        raise RuntimeError("fanout exploded")

    monkeypatch.setattr(
        "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
    )
    monkeypatch.setattr("ralph.agents.subprocess_executor.SubprocessAgentExecutor", _FakeExecutor)
    monkeypatch.setattr("ralph.git.worktree_manager.WorktreeManager", _FakeWorktreeManager)
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
    )
    monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _boom)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

    recovered = runner_module._execute_fan_out_sync(
        effect=effect,
        state=state,
        display=runner_module._LegacyConsoleDisplay(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    assert recovered.phase == PHASE_DEVELOPMENT
    assert recovered.dev_chain.retries == 1
    assert recovered.recovery_epoch == 0
    assert recovered.last_error is not None
    assert "Fan-out execution crashed" in recovered.last_error
    assert "fanout exploded" in recovered.last_error


def test_execute_fan_out_sync_requeues_running_workers_via_reducer_event(
    monkeypatch, tmp_path
) -> None:
    resumed_event = getattr(PipelineEvent, "WORKERS_RESUMED", None)
    assert resumed_event is not None

    unit = _make_work_unit("unit-a")
    effect = FanOutDevelopmentEffect(work_units=(unit,), max_workers=1)
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        work_units=(unit,),
        worker_states={"unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.RUNNING)},
    )
    policy_bundle = _make_policy_bundle(max_workers=1)
    workspace_scope = WorkspaceScope(tmp_path)
    seen_events: list[object] = []
    original_reduce = runner_module.reducer_reduce

    class _FakeExecutor:
        def __init__(self, command, signal_bridge=None) -> None:
            del command, signal_bridge

    class _FakeWorktreeManager:
        def __init__(self, repo_root, git) -> None:
            del repo_root, git

    class _FakeMcpFactory:
        def __init__(self, workspace) -> None:
            del workspace

    async def _fake_run_fan_out(**kwargs):
        del kwargs
        return []

    async def _fake_integrate(**kwargs):
        del kwargs
        return SimpleNamespace(events=[])

    def _recording_reduce(current_state, event, pipeline_policy=None):
        seen_events.append(event)
        return original_reduce(current_state, event, pipeline_policy)

    monkeypatch.setattr(
        "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
    )
    monkeypatch.setattr("ralph.agents.subprocess_executor.SubprocessAgentExecutor", _FakeExecutor)
    monkeypatch.setattr("ralph.git.worktree_manager.WorktreeManager", _FakeWorktreeManager)
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
    )
    monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
    monkeypatch.setattr("ralph.pipeline.parallel.merge_integrator.integrate", _fake_integrate)
    monkeypatch.setattr(runner_module, "reducer_reduce", _recording_reduce)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

    result = runner_module._execute_fan_out_sync(
        effect=effect,
        state=state,
        display=runner_module._LegacyConsoleDisplay(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    assert seen_events[0] == resumed_event
    assert result.worker_states["unit-a"].status == WorkerStatus.PENDING


def test_execute_fan_out_sync_uses_parallel_display_subscriber_when_not_provided(
    monkeypatch, tmp_path
) -> None:
    unit = _make_work_unit("unit-a")
    effect = FanOutDevelopmentEffect(work_units=(unit,), max_workers=1)
    state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=(unit,))
    policy_bundle = _make_policy_bundle(max_workers=1)
    workspace_scope = WorkspaceScope(tmp_path)
    notified_phases: list[str] = []

    class _Subscriber:
        def notify(self, state: PipelineState) -> None:
            notified_phases.append(state.phase)

    class _FakeParallelDisplay:
        def __init__(self, *_args, **_kwargs) -> None:
            self.subscriber = _Subscriber()

    class _FakeExecutor:
        def __init__(self, command, signal_bridge=None) -> None:
            del command, signal_bridge

    class _FakeWorktreeManager:
        def __init__(self, repo_root, git) -> None:
            del repo_root, git

    class _FakeMcpFactory:
        def __init__(self, workspace) -> None:
            del workspace

    async def _fake_run_fan_out(**kwargs):
        del kwargs
        return [PipelineEvent.AGENT_SUCCESS]

    async def _fake_integrate(**kwargs):
        del kwargs
        return SimpleNamespace(events=[PipelineEvent.ALL_WORKERS_COMPLETE])

    monkeypatch.setattr(
        "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
    )
    monkeypatch.setattr("ralph.display.parallel_display.ParallelDisplay", _FakeParallelDisplay)
    monkeypatch.setattr("ralph.agents.subprocess_executor.SubprocessAgentExecutor", _FakeExecutor)
    monkeypatch.setattr("ralph.git.worktree_manager.WorktreeManager", _FakeWorktreeManager)
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
    )
    monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
    monkeypatch.setattr("ralph.pipeline.parallel.merge_integrator.integrate", _fake_integrate)
    monkeypatch.setattr(
        runner_module,
        "reducer_reduce",
        lambda current_state, *_args, **_kwargs: (current_state, None),
    )
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

    runner_module._execute_fan_out_sync(
        effect=effect,
        state=state,
        display=runner_module._LegacyConsoleDisplay(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
        dashboard_subscriber=None,
    )

    assert notified_phases == [state.phase, state.phase, state.phase]


def test_execute_fan_out_sync_notifies_dashboard_subscriber_after_each_reduce(
    monkeypatch, tmp_path
) -> None:
    unit = _make_work_unit("unit-a")
    effect = FanOutDevelopmentEffect(work_units=(unit,), max_workers=1)
    state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=(unit,))
    policy_bundle = _make_policy_bundle(max_workers=1)
    workspace_scope = WorkspaceScope(tmp_path)
    reduced_phases: list[str] = []
    notified_phases: list[str] = []

    class _Subscriber:
        def notify(self, state: PipelineState) -> None:
            notified_phases.append(state.phase)

    class _FakeExecutor:
        def __init__(self, command, signal_bridge=None) -> None:
            del command, signal_bridge

    class _FakeWorktreeManager:
        def __init__(self, repo_root, git) -> None:
            del repo_root, git

    class _FakeMcpFactory:
        def __init__(self, workspace) -> None:
            del workspace

    async def _fake_run_fan_out(**kwargs):
        del kwargs
        return [PipelineEvent.AGENT_SUCCESS]

    async def _fake_integrate(**kwargs):
        del kwargs
        return SimpleNamespace(events=[PipelineEvent.ALL_WORKERS_COMPLETE])

    def _recording_reduce(current_state, event, pipeline_policy=None):
        del event, pipeline_policy
        next_state = current_state.copy_with(phase=current_state.phase)
        reduced_phases.append(next_state.phase)
        return next_state, None

    monkeypatch.setattr(
        "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
    )
    monkeypatch.setattr("ralph.agents.subprocess_executor.SubprocessAgentExecutor", _FakeExecutor)
    monkeypatch.setattr("ralph.git.worktree_manager.WorktreeManager", _FakeWorktreeManager)
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
    )
    monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
    monkeypatch.setattr("ralph.pipeline.parallel.merge_integrator.integrate", _fake_integrate)
    monkeypatch.setattr(runner_module, "reducer_reduce", _recording_reduce)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

    runner_module._execute_fan_out_sync(
        effect=effect,
        state=state,
        display=runner_module._LegacyConsoleDisplay(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
        dashboard_subscriber=_Subscriber(),
    )

    assert notified_phases == reduced_phases
