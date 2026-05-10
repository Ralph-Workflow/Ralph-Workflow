"""Integration tests for fan-out wiring in pipeline runner."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import FanOutEffect, InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus
from ralph.policy.models import PhaseParallelization
from ralph.workspace.scope import WorkspaceScope


def _legacy_display() -> runner_module._LegacyConsoleDisplay:
    return runner_module._LegacyConsoleDisplay(make_display_context())


def _make_work_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(
        unit_id=unit_id,
        description=f"Work unit {unit_id}",
        allowed_directories=[f"src/{unit_id}"],
    )


def _make_policy_bundle(max_workers: int = 4) -> MagicMock:
    bundle = MagicMock()
    para = PhaseParallelization(max_parallel_workers=max_workers, post_fanout_verification=False)
    dev_phase = MagicMock(requires_commit=False, drain="development", role="execution")
    dev_phase.parallelization = para
    plan_phase = MagicMock(requires_commit=False, drain="planning", role="execution")
    plan_phase.parallelization = None
    bundle.pipeline.phases = {
        "development": dev_phase,
        "planning": plan_phase,
    }
    bundle.agents.agent_drains = {
        "development": MagicMock(
            chain="developer", drain_class="development", capability_class=None
        ),
        "planning": MagicMock(
            chain="planner", drain_class="planning", capability_class=None
        ),
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
        state = PipelineState(phase="development", work_units=())
        policy_bundle = _make_policy_bundle()

        effect = runner_module._determine_effect_from_policy(
            state,
            policy_bundle,
            config=UnifiedConfig(),
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.phase == "development"

    def test_fanout_when_work_units_present(self) -> None:
        """When work_units present, development phase uses FanOutEffect."""
        units = (
            _make_work_unit("unit-a"),
            _make_work_unit("unit-b"),
        )
        max_workers = 3
        state = PipelineState(phase="development", work_units=units)
        policy_bundle = _make_policy_bundle(max_workers=max_workers)

        effect = runner_module._determine_effect_from_policy(
            state,
            policy_bundle,
            config=UnifiedConfig(),
        )

        assert isinstance(effect, FanOutEffect)
        assert effect.work_units == units
        assert effect.max_workers == max_workers

    def test_serial_when_single_work_unit(self) -> None:
        """Single work_unit falls through to InvokeAgentEffect — fan-out requires >=2 units."""
        state = PipelineState(phase="development", work_units=(_make_work_unit("unit-a"),))
        policy_bundle = _make_policy_bundle()

        effect = runner_module._determine_effect_from_policy(
            state,
            policy_bundle,
            config=UnifiedConfig(),
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.phase == "development"

    def test_non_development_phase_not_affected(self) -> None:
        """Other phases always use InvokeAgentEffect regardless of work_units."""
        units = (_make_work_unit("unit-a"),)
        state = PipelineState(phase="planning", work_units=units)
        policy_bundle = _make_policy_bundle()

        effect = runner_module._determine_effect_from_policy(
            state,
            policy_bundle,
            config=UnifiedConfig(),
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.phase == "planning"


def test_execute_fan_out_sync_wires_signal_handlers_and_same_workspace_context(
    monkeypatch, tmp_path
) -> None:
    unit = _make_work_unit("unit-a")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)
    state = PipelineState(
        phase="development",
        work_units=(unit,),
        phase_chains={"development": AgentChainState(agents=["claude"])},
    )
    policy_bundle = _make_policy_bundle(max_workers=1)
    workspace_scope = WorkspaceScope(tmp_path)
    install_calls: list[tuple[object, object, object]] = []
    coordinator_calls: list[dict[str, object]] = []
    executor_calls: list[dict[str, object]] = []
    mcp_factory_calls: list[dict[str, object]] = []

    class _FakeExecutor:
        def __init__(self, command, signal_bridge=None) -> None:
            executor_calls.append({"command": tuple(command), "signal_bridge": signal_bridge})

    class _FakeMcpFactory:
        def __init__(self, workspace) -> None:
            mcp_factory_calls.append({"workspace": workspace})

    def _fake_install(loop, root_task, bridge) -> None:
        install_calls.append((loop, root_task, bridge))

    async def _fake_run_fan_out(**kwargs):
        coordinator_calls.append(kwargs)
        return []

    monkeypatch.setattr("ralph.interrupt.asyncio_bridge.install_signal_handlers", _fake_install)
    monkeypatch.setattr("ralph.agents.subprocess_executor.SubprocessAgentExecutor", _FakeExecutor)
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
    )
    monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

    runner_module._execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    assert len(install_calls) == 1
    assert len(executor_calls) == 1
    assert executor_calls[0]["signal_bridge"] is install_calls[0][2]
    assert mcp_factory_calls
    ctx = cast("Any", coordinator_calls[0]["ctx"])
    assert ctx.same_workspace is not None
    # Verify session contract fields are properly threaded from the runner's
    # _build_session_mcp_plan_for_phase into SameWorkspaceContext.
    assert ctx.same_workspace.session_drain == "development"
    assert "media.read" in ctx.same_workspace.session_capabilities
    assert ctx.same_workspace.session_model_identity is not None
    assert ctx.same_workspace.session_capability_profile is not None


def test_execute_fan_out_sync_converts_unexpected_coordinator_error_to_failed_recovery_state(
    monkeypatch, tmp_path
) -> None:
    unit = _make_work_unit("unit-a")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)
    state = PipelineState(
        phase="development",
        work_units=(unit,),
        phase_chains={"development": AgentChainState(agents=["claude"])},
    )
    policy_bundle = _make_policy_bundle(max_workers=1)
    workspace_scope = WorkspaceScope(tmp_path)

    class _FakeExecutor:
        def __init__(self, command, signal_bridge=None) -> None:
            del command, signal_bridge

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
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
    )
    monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _boom)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

    recovered = runner_module._execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    assert recovered.phase == "development"
    assert recovered.chain_for_phase("development").retries == 1
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
    effect = FanOutEffect(work_units=(unit,), max_workers=1)
    state = PipelineState(
        phase="development",
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

    class _FakeMcpFactory:
        def __init__(self, workspace) -> None:
            del workspace

    async def _fake_run_fan_out(**kwargs):
        del kwargs
        return []

    def _recording_reduce(current_state, event, pipeline_policy=None):
        seen_events.append(event)
        return original_reduce(current_state, event, pipeline_policy)

    monkeypatch.setattr(
        "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
    )
    monkeypatch.setattr("ralph.agents.subprocess_executor.SubprocessAgentExecutor", _FakeExecutor)
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
    )
    monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
    monkeypatch.setattr(runner_module, "reducer_reduce", _recording_reduce)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

    result = runner_module._execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    assert seen_events[0] == resumed_event
    assert result.worker_states["unit-a"].status == WorkerStatus.PENDING


def test_execute_fan_out_sync_uses_parallel_display_subscriber_when_not_provided(
    monkeypatch, tmp_path
) -> None:
    unit = _make_work_unit("unit-a")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)
    state = PipelineState(phase="development", work_units=(unit,))
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

    class _FakeMcpFactory:
        def __init__(self, workspace) -> None:
            del workspace

    async def _fake_run_fan_out(**kwargs):
        del kwargs
        return [PipelineEvent.AGENT_SUCCESS]

    monkeypatch.setattr(
        "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
    )
    monkeypatch.setattr("ralph.display.parallel_display.ParallelDisplay", _FakeParallelDisplay)
    monkeypatch.setattr("ralph.agents.subprocess_executor.SubprocessAgentExecutor", _FakeExecutor)
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
    )
    monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
    monkeypatch.setattr(
        runner_module,
        "reducer_reduce",
        lambda current_state, *_args, **_kwargs: (current_state, None),
    )
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

    runner_module._execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
        dashboard_subscriber=None,
    )

    # WORKERS_RESUMED notification + one per fan_out_events entry (AGENT_SUCCESS)
    assert notified_phases == [state.phase, state.phase]


def test_execute_fan_out_sync_notifies_dashboard_subscriber_after_each_reduce(
    monkeypatch, tmp_path
) -> None:
    unit = _make_work_unit("unit-a")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)
    state = PipelineState(phase="development", work_units=(unit,))
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

    class _FakeMcpFactory:
        def __init__(self, workspace) -> None:
            del workspace

    async def _fake_run_fan_out(**kwargs):
        del kwargs
        return [PipelineEvent.AGENT_SUCCESS]

    def _recording_reduce(current_state, event, pipeline_policy=None):
        del event, pipeline_policy
        next_state = current_state.copy_with(phase=current_state.phase)
        reduced_phases.append(next_state.phase)
        return next_state, None

    monkeypatch.setattr(
        "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
    )
    monkeypatch.setattr("ralph.agents.subprocess_executor.SubprocessAgentExecutor", _FakeExecutor)
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
    )
    monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
    monkeypatch.setattr(runner_module, "reducer_reduce", _recording_reduce)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

    runner_module._execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
        dashboard_subscriber=_Subscriber(),
    )

    assert notified_phases == reduced_phases

def test_materialize_prepared_prompt_uses_worker_namespace_from_env(
    monkeypatch, tmp_path
) -> None:
    """When RALPH_WORKER_NAMESPACE is set, prompt payloads land in the worker's namespace."""
    from ralph.pipeline import runner as runner_module  # noqa: PLC0415
    from ralph.pipeline.effects import PreparePromptEffect  # noqa: PLC0415
    from ralph.policy.loader import load_policy  # noqa: PLC0415

    worker_ns = tmp_path / ".agent" / "workers" / "unit-test"
    worker_ns.mkdir(parents=True, exist_ok=True)
    shared_payload_dir = tmp_path / ".agent" / "tmp" / "prompt_payloads"

    recorded_kwargs: list[dict] = []

    def _fake_materialize(**kwargs):
        recorded_kwargs.append(kwargs)
        return "rendered-prompt"

    def _fake_dump(workspace, phase, prompt):
        return "/fake/prompt/path"

    monkeypatch.setenv("RALPH_WORKER_NAMESPACE", str(worker_ns))
    monkeypatch.setattr(runner_module, "materialize_prompt_for_phase", _fake_materialize)
    # Patch dump to avoid writing files
    import ralph.prompts.materialize  # noqa: PLC0415
    monkeypatch.setattr(ralph.prompts.materialize, "dump_rendered_prompt", _fake_dump)

    policy = load_policy(tmp_path / ".agent")
    workspace_scope = WorkspaceScope(tmp_path)
    effect = PreparePromptEffect(phase="development", iteration=1)

    runner_module._materialize_prepared_prompt(
        effect, policy.pipeline, policy.artifacts, workspace_scope
    )

    assert len(recorded_kwargs) == 1
    wn = recorded_kwargs[0].get("worker_namespace")
    assert wn is not None, "worker_namespace must be passed to materialize_prompt_for_phase"
    assert wn == worker_ns, f"Expected {worker_ns}, got {wn}"
    assert not shared_payload_dir.exists(), (
        "Shared payload dir must not be written when worker_namespace is set"
    )


def test_materialize_prepared_prompt_no_namespace_without_env(
    monkeypatch, tmp_path
) -> None:
    """Without RALPH_WORKER_NAMESPACE, worker_namespace is None (shared path used)."""
    from ralph.pipeline import runner as runner_module  # noqa: PLC0415
    from ralph.pipeline.effects import PreparePromptEffect  # noqa: PLC0415
    from ralph.policy.loader import load_policy  # noqa: PLC0415

    monkeypatch.delenv("RALPH_WORKER_NAMESPACE", raising=False)
    recorded_kwargs: list[dict] = []

    def _fake_materialize(**kwargs):
        recorded_kwargs.append(kwargs)
        return "rendered-prompt"

    import ralph.prompts.materialize  # noqa: PLC0415
    monkeypatch.setattr(runner_module, "materialize_prompt_for_phase", _fake_materialize)
    monkeypatch.setattr(ralph.prompts.materialize, "dump_rendered_prompt", lambda *a, **k: "/p")

    policy = load_policy(tmp_path / ".agent")
    workspace_scope = WorkspaceScope(tmp_path)
    effect = PreparePromptEffect(phase="development", iteration=1)

    runner_module._materialize_prepared_prompt(
        effect, policy.pipeline, policy.artifacts, workspace_scope
    )

    assert len(recorded_kwargs) == 1
    assert recorded_kwargs[0].get("worker_namespace") is None
