"""Integration tests for fan-out wiring in pipeline runner."""

from __future__ import annotations

from typing import TYPE_CHECKING, Never, cast
from unittest.mock import MagicMock

import ralph.prompts.materialize
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.protocol.env import WORKER_NAMESPACE_ENV
from ralph.pipeline import fan_out as fan_out_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import FanOutEffect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.parallel.worker_manifest import ParallelWorkerManifest
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseParallelization
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from ralph.pipeline.events import Event
    from ralph.pipeline.parallel.coordinator import WorkerContext
    from ralph.policy.models import PipelinePolicy


def _legacy_display() -> runner_module.LegacyConsoleDisplay:
    return runner_module.LegacyConsoleDisplay(make_display_context())


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
        "planning": MagicMock(chain="planner", drain_class="planning", capability_class=None),
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

        effect = runner_module.determine_effect_from_policy(
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

        effect = runner_module.determine_effect_from_policy(
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

        effect = runner_module.determine_effect_from_policy(
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

        effect = runner_module.determine_effect_from_policy(
            state,
            policy_bundle,
            config=UnifiedConfig(),
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.phase == "planning"


def test_execute_fan_out_sync_wires_signal_handlers_and_same_workspace_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    units = (_make_work_unit("unit-a"), _make_work_unit("unit-b"))
    effect = FanOutEffect(work_units=units, max_workers=2)
    state = PipelineState(
        phase="development",
        work_units=units,
        phase_chains={"development": AgentChainState(agents=["claude"])},
    )
    policy_bundle = _make_policy_bundle(max_workers=2)
    workspace_scope = WorkspaceScope(tmp_path)
    install_calls: list[tuple[object, object, object]] = []
    coordinator_calls: list[dict[str, object]] = []
    executor_calls: list[dict[str, object]] = []
    mcp_factory_calls: list[dict[str, object]] = []

    class _FakeExecutor:
        def __init__(self, command: object, signal_bridge: object | None = None) -> None:
            executor_calls.append(
                {
                    "command": tuple(cast("tuple[str, ...]", command)),
                    "signal_bridge": signal_bridge,
                }
            )

    class _FakeMcpFactory:
        def __init__(self, workspace: object) -> None:
            mcp_factory_calls.append({"workspace": workspace})

    def _fake_install(loop: object, root_task: object, bridge: object) -> None:
        install_calls.append((loop, root_task, bridge))

    async def _fake_run_fan_out(**kwargs: object) -> list[object]:
        coordinator_calls.append(dict(kwargs))
        return []

    monkeypatch.setattr(runner_module, "install_signal_handlers", _fake_install)
    monkeypatch.setattr(runner_module, "SubprocessAgentExecutor", _FakeExecutor)
    monkeypatch.setattr(runner_module, "DynamicBindingMcpServerFactory", _FakeMcpFactory)
    monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

    runner_module.execute_fan_out_sync(
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
    ctx = cast("WorkerContext", coordinator_calls[0]["ctx"])
    assert ctx.same_workspace is not None
    # Verify session contract fields are properly threaded from the runner's
    # _build_session_mcp_plan_for_phase into SameWorkspaceContext.
    assert ctx.same_workspace.session_drain == "development"
    assert "media.read" in ctx.same_workspace.session_capabilities
    assert ctx.same_workspace.session_model_identity is not None
    assert ctx.same_workspace.session_capability_profile is not None
    assert ctx.same_workspace.worker_commands["unit-a"] == (
        fan_out_module.sys.executable,
        "-m",
        "ralph",
        "--parallel-worker-manifest",
        str(tmp_path / ".agent" / "workers" / "unit-a" / "worker-manifest.json"),
    )
    assert (
        ctx.same_workspace.worker_commands["unit-a"]
        != ctx.same_workspace.worker_commands["unit-b"]
    )
    assert (
        ctx.same_workspace.worker_manifest_paths["unit-a"]
        != ctx.same_workspace.worker_manifest_paths["unit-b"]
    )


def test_execute_fan_out_sync_persists_worker_manifests_with_distinct_phase_and_drain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unit = _make_work_unit("unit-a")
    effect = FanOutEffect(work_units=(unit,), max_workers=1, phase="parallel-dev")
    state = PipelineState(
        phase="parallel-dev",
        work_units=(unit,),
        phase_chains={"parallel-dev": AgentChainState(agents=["claude"])},
    )
    policy_bundle = _make_policy_bundle(max_workers=1)
    parallel_phase = MagicMock(requires_commit=False, drain="development", role="execution")
    parallel_phase.parallelization = policy_bundle.pipeline.phases["development"].parallelization
    policy_bundle.pipeline.phases["parallel-dev"] = parallel_phase
    workspace_scope = WorkspaceScope(tmp_path)
    coordinator_calls: list[dict[str, object]] = []

    class _FakeExecutor:
        def __init__(self, command: object, signal_bridge: object | None = None) -> None:
            del command, signal_bridge

    class _FakeMcpFactory:
        def __init__(self, workspace: object) -> None:
            del workspace

    async def _fake_run_fan_out(**kwargs: object) -> list[object]:
        coordinator_calls.append(dict(kwargs))
        return []

    monkeypatch.setattr(
        "ralph.interrupt.asyncio_bridge.install_signal_handlers", lambda *args: None
    )
    monkeypatch.setattr("ralph.agents.subprocess_executor.SubprocessAgentExecutor", _FakeExecutor)
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory", _FakeMcpFactory
    )
    monkeypatch.setattr("ralph.pipeline.parallel.coordinator.run_fan_out", _fake_run_fan_out)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

    runner_module.execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    ctx = cast("WorkerContext", coordinator_calls[0]["ctx"])
    same_workspace = ctx.same_workspace
    assert same_workspace is not None
    manifest_path = same_workspace.worker_manifest_paths[unit.unit_id]
    manifest = ParallelWorkerManifest.load(manifest_path)
    assert manifest.phase == "parallel-dev"
    assert manifest.drain == "development"
    assert manifest.worker_namespace == str(tmp_path / ".agent" / "workers" / unit.unit_id)


def test_execute_fan_out_sync_converts_unexpected_coordinator_error_to_failed_recovery_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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
        def __init__(self, command: object, signal_bridge: object | None = None) -> None:
            del command, signal_bridge

    class _FakeMcpFactory:
        def __init__(self, workspace: object) -> None:
            del workspace

    async def _boom(**kwargs: object) -> Never:
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

    recovered = runner_module.execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    assert recovered.phase == "development"
    development_chain = recovered.chain_for_phase("development")
    assert development_chain is not None
    assert development_chain.retries == 1
    assert recovered.recovery_epoch == 0
    assert recovered.last_error is not None
    assert "Fan-out execution crashed" in recovered.last_error
    assert "fanout exploded" in recovered.last_error


def test_execute_fan_out_sync_requeues_running_workers_via_reducer_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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
        def __init__(self, command: object, signal_bridge: object | None = None) -> None:
            del command, signal_bridge

    class _FakeMcpFactory:
        def __init__(self, workspace: object) -> None:
            del workspace

    async def _fake_run_fan_out(**kwargs: object) -> list[object]:
        del kwargs
        return []

    def _recording_reduce(
        current_state: PipelineState,
        event: Event,
        pipeline_policy: PipelinePolicy | None = None,
    ) -> tuple[PipelineState, object]:
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

    result = runner_module.execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    assert seen_events[0] == resumed_event
    assert result.worker_states["unit-a"].status == WorkerStatus.PENDING


def test_execute_fan_out_sync_uses_parallel_display_subscriber_when_not_provided(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.subscriber = _Subscriber()

    class _FakeExecutor:
        def __init__(self, command: object, signal_bridge: object | None = None) -> None:
            del command, signal_bridge

    class _FakeMcpFactory:
        def __init__(self, workspace: object) -> None:
            del workspace

    async def _fake_run_fan_out(**kwargs: object) -> list[PipelineEvent]:
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

    runner_module.execute_fan_out_sync(
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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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
        def __init__(self, command: object, signal_bridge: object | None = None) -> None:
            del command, signal_bridge

    class _FakeMcpFactory:
        def __init__(self, workspace: object) -> None:
            del workspace

    async def _fake_run_fan_out(**kwargs: object) -> list[PipelineEvent]:
        del kwargs
        return [PipelineEvent.AGENT_SUCCESS]

    def _recording_reduce(
        current_state: PipelineState,
        event: object,
        pipeline_policy: object | None = None,
    ) -> tuple[PipelineState, None]:
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

    runner_module.execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_legacy_display(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
        dashboard_subscriber=_Subscriber(),
    )

    assert notified_phases == reduced_phases


def test_materialize_prepared_prompt_uses_worker_namespace_from_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When RALPH_WORKER_NAMESPACE is set, prompt payloads land in the worker's namespace."""

    worker_ns = tmp_path / ".agent" / "workers" / "unit-test"
    worker_ns.mkdir(parents=True, exist_ok=True)
    shared_payload_dir = tmp_path / ".agent" / "tmp" / "prompt_payloads"

    recorded_kwargs: list[dict[str, object]] = []

    def _fake_materialize(**kwargs: object) -> str:
        recorded_kwargs.append(dict(kwargs))
        return "rendered-prompt"

    def _fake_dump(workspace: object, phase: object, prompt: object) -> str:
        del workspace, phase, prompt
        return "/fake/prompt/path"

    # Patch materialize and dump to avoid writing files

    monkeypatch.setattr(runner_module, "materialize_prompt_for_phase", _fake_materialize)
    monkeypatch.setattr(ralph.prompts.materialize, "dump_rendered_prompt", _fake_dump)

    policy = load_policy(tmp_path / ".agent")
    workspace_scope = WorkspaceScope(tmp_path)
    effect = PreparePromptEffect(phase="development", iteration=1)

    runner_module.materialize_prepared_prompt(
        effect,
        policy.pipeline,
        policy.artifacts,
        workspace_scope,
        env={str(WORKER_NAMESPACE_ENV): str(worker_ns)},
    )

    assert len(recorded_kwargs) == 1
    wn = recorded_kwargs[0].get("worker_namespace")
    assert wn is not None, "worker_namespace must be passed to materialize_prompt_for_phase"
    assert wn == worker_ns, f"Expected {worker_ns}, got {wn}"
    assert not shared_payload_dir.exists(), (
        "Shared payload dir must not be written when worker_namespace is set"
    )


def test_materialize_prepared_prompt_no_namespace_without_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Without RALPH_WORKER_NAMESPACE, worker_namespace is None (shared path used)."""

    monkeypatch.delenv(str(WORKER_NAMESPACE_ENV), raising=False)
    recorded_kwargs: list[dict[str, object]] = []

    def _fake_materialize(**kwargs: object) -> str:
        recorded_kwargs.append(dict(kwargs))
        return "rendered-prompt"

    monkeypatch.setattr(runner_module, "materialize_prompt_for_phase", _fake_materialize)
    monkeypatch.setattr(ralph.prompts.materialize, "dump_rendered_prompt", lambda *a, **k: "/p")

    policy = load_policy(tmp_path / ".agent")
    workspace_scope = WorkspaceScope(tmp_path)
    effect = PreparePromptEffect(phase="development", iteration=1)

    runner_module.materialize_prepared_prompt(
        effect, policy.pipeline, policy.artifacts, workspace_scope
    )

    assert len(recorded_kwargs) == 1
    assert recorded_kwargs[0].get("worker_namespace") is None
