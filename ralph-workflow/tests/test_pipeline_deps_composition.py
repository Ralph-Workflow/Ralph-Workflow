"""Unit tests for ``PipelineDeps`` composition with fake collaborators.

These tests verify that the pipeline dependency bundle can be built from
production defaults, overridden field-by-field, and wired to fake
collaborators without real subprocess, network, or wall-clock delays.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import PipelineDeps, build_default_pipeline_deps
from ralph.pipeline.work_unit import WorkUnit
from ralph.pro_support.hooks import ProPipelineHooks
from ralph.pro_support.state_query import PipelineStateSnapshot, SnapshotRegistry
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun
from ralph.workspace.memory import MemoryWorkspace
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from ralph.agents.executor import WorkerResult
    from ralph.display.context import DisplayContext


def _fake_display_context() -> DisplayContext:
    return make_display_context(env={"NO_COLOR": "1", "COLUMNS": "120"})


def test_build_default_pipeline_deps_returns_valid_pipeline_deps() -> None:
    """``build_default_pipeline_deps`` returns a ``PipelineDeps`` with all
    required fields wired to production defaults.
    """
    config = UnifiedConfig()
    display_context = _fake_display_context()

    deps = build_default_pipeline_deps(config, display_context)

    assert isinstance(deps, PipelineDeps)
    assert deps.display_context is display_context
    assert deps.model_identity is None
    assert deps.registry_factory is None
    assert callable(deps.master_prompt_materializer)
    assert callable(deps.phase_prompt_materializer)
    assert callable(deps.artifact_requirements_resolver)
    assert callable(deps.bridge_factory)
    assert callable(deps.mcp_supervisor_factory)
    assert callable(deps.heartbeat_policy_from_env_fn)
    assert callable(deps.check_mcp_bridge_health_fn)
    assert deps.policy_bundle is None
    assert deps.policy_bundle_factory is None
    assert deps.state_factory is None
    assert deps.recovery_controller_factory is None
    assert deps.marker_watcher_factory is None
    assert deps.snapshot_registry is None
    assert deps.recovery_sleep is None


def test_test_pipeline_deps_fakes_external_runtime_boundaries() -> None:
    """The shared test bundle never starts network or process-tree I/O."""
    deps = make_test_pipeline_deps(_fake_display_context())

    assert deps.connectivity_monitor.current_state.value == "online"
    assert deps.catchup_worker_factory(UnifiedConfig(), Path("/workspace")) is None
    assert deps.startup_rebase_resolver(UnifiedConfig(), MagicMock()) is None
    assert deps.auto_integrate_resolver(UnifiedConfig(), MagicMock(), MagicMock()) is None
    assert (
        deps.commit_effect_executor(MagicMock(), Path("/workspace"))
        is PipelineEvent.COMMIT_SKIPPED
    )
    assert deps.process_teardown is not None
    assert deps.process_teardown() is None


def test_build_default_pipeline_deps_applies_pro_hooks() -> None:
    """``build_default_pipeline_deps`` applies ``ProPipelineHooks`` overrides."""
    config = UnifiedConfig()
    display_context = _fake_display_context()
    registry_factory = MagicMock()
    state_factory = MagicMock()
    snapshot_registry = SnapshotRegistry()

    def recovery_sleep(_seconds: float) -> None:
        return None

    pro_hooks = ProPipelineHooks(
        registry_factory=registry_factory,
        state_factory=state_factory,
        snapshot_registry=snapshot_registry,
        recovery_sleep=recovery_sleep,
    )

    deps = build_default_pipeline_deps(config, display_context, pro_hooks=pro_hooks)

    assert deps.registry_factory is registry_factory
    assert deps.state_factory is state_factory
    assert deps.snapshot_registry is snapshot_registry
    assert deps.recovery_sleep is recovery_sleep


def test_pipeline_deps_replace_overrides_individual_fields() -> None:
    """``dataclasses.replace`` correctly overrides individual collaborators."""
    config = UnifiedConfig()
    display_context = _fake_display_context()
    deps = build_default_pipeline_deps(config, display_context)

    new_display_context = _fake_display_context()
    new_registry_factory = MagicMock()
    new_master_prompt_materializer = MagicMock(return_value="/fake/master_prompt.md")
    new_bridge_factory = MagicMock()

    overridden = dataclasses.replace(
        deps,
        display_context=new_display_context,
        registry_factory=new_registry_factory,
        master_prompt_materializer=new_master_prompt_materializer,
        bridge_factory=new_bridge_factory,
    )

    assert overridden.display_context is new_display_context
    assert overridden.registry_factory is new_registry_factory
    assert overridden.master_prompt_materializer is new_master_prompt_materializer
    assert overridden.bridge_factory is new_bridge_factory
    # Unchanged fields retain production defaults.
    assert overridden.model_identity is None
    assert overridden.phase_prompt_materializer is deps.phase_prompt_materializer
    assert overridden.artifact_requirements_resolver is deps.artifact_requirements_resolver


def test_pipeline_deps_replace_preserves_immutability() -> None:
    """Replacing a field returns a new instance; the original is unchanged."""
    config = UnifiedConfig()
    display_context = _fake_display_context()
    deps = build_default_pipeline_deps(config, display_context)

    new_registry_factory = MagicMock()
    overridden = dataclasses.replace(deps, registry_factory=new_registry_factory)

    assert overridden is not deps
    assert deps.registry_factory is None
    assert overridden.registry_factory is new_registry_factory


def test_pro_pipeline_hooks_to_runner_kwargs_returns_six_fields() -> None:
    """``ProPipelineHooks.to_runner_kwargs`` returns the six factory kwargs."""
    hooks = ProPipelineHooks(
        policy_bundle_factory=MagicMock(),
        registry_factory=MagicMock(),
        state_factory=MagicMock(),
        recovery_controller_factory=MagicMock(),
        marker_watcher_factory=MagicMock(),
        snapshot_registry=SnapshotRegistry(),
    )

    kwargs = hooks.to_runner_kwargs()

    assert set(kwargs) == {
        "policy_bundle_factory",
        "registry_factory",
        "state_factory",
        "recovery_controller_factory",
        "marker_watcher_factory",
        "snapshot_registry",
    }
    assert kwargs["policy_bundle_factory"] is hooks.policy_bundle_factory
    assert kwargs["registry_factory"] is hooks.registry_factory
    assert kwargs["state_factory"] is hooks.state_factory
    assert kwargs["recovery_controller_factory"] is hooks.recovery_controller_factory
    assert kwargs["marker_watcher_factory"] is hooks.marker_watcher_factory
    assert kwargs["snapshot_registry"] is hooks.snapshot_registry


def test_pro_pipeline_hooks_override_excludes_policy_bundle_override() -> None:
    """``policy_bundle_override`` is intentionally excluded from runner kwargs."""
    policy_bundle = MagicMock()
    hooks = ProPipelineHooks(policy_bundle_override=policy_bundle)

    kwargs = hooks.to_runner_kwargs()

    assert "policy_bundle_override" not in kwargs
    assert all(value is None for value in kwargs.values())


def test_snapshot_registry_publish_and_get_latest() -> None:
    """``SnapshotRegistry`` stores the latest snapshot and returns it via
    ``get_latest``.
    """
    registry = SnapshotRegistry()
    snapshot = PipelineStateSnapshot(
        phase="development",
        previous_phase=None,
        run_id="run-1",
        interrupted_by_user=False,
        last_error=None,
        metrics={"iterations": 1},
        budget_caps={"analysis": 5},
        outer_progress={"iteration": 1},
        loop_iterations={"analysis_iteration": 0},
        iteration=1,
        analysis_iteration=0,
    )

    registry.publish(snapshot)
    latest = registry.get_latest()

    assert latest is not None
    assert latest == snapshot
    assert latest is not snapshot


def test_snapshot_registry_publish_defensive_copy() -> None:
    """``publish`` stores a field-by-field copy, isolating the registry from
    later mutations of the published snapshot.
    """
    registry = SnapshotRegistry()
    mutable_metrics: dict[str, int] = {"iterations": 1}
    snapshot = PipelineStateSnapshot(
        phase="development",
        previous_phase=None,
        run_id="run-1",
        interrupted_by_user=False,
        last_error=None,
        metrics=mutable_metrics,
        budget_caps={"analysis": 5},
        outer_progress={"iteration": 1},
        loop_iterations={"analysis_iteration": 0},
        iteration=1,
        analysis_iteration=0,
    )

    registry.publish(snapshot)
    mutable_metrics["iterations"] = 99

    latest = registry.get_latest()
    assert latest is not None
    assert latest.metrics["iterations"] == 1


def test_pipeline_deps_accepts_fake_collaborators() -> None:
    """``PipelineDeps`` can be constructed entirely from fake collaborators.

    This test wires every field to a lightweight fake so the bundle can be
    passed to execution paths in tests without real I/O or subprocesses.
    """
    workspace = MemoryWorkspace()
    display_context = _fake_display_context()

    def fake_master_prompt_materializer(
        workspace_root: Path,
        name: str,
        default_product_criteria: str | None = None,
        worker_namespace: Path | None = None,
    ) -> str:
        del workspace_root, default_product_criteria, worker_namespace
        path = f".agent/tmp/master_prompt_{name}.md"
        workspace.write(path, "fake master prompt")
        return path

    deps = PipelineDeps(
        display_context=display_context,
        model_identity=None,
        registry_factory=lambda _config: MagicMock(),
        master_prompt_materializer=fake_master_prompt_materializer,
        phase_prompt_materializer=MagicMock(return_value=".agent/tmp/phase_prompt.md"),
        artifact_requirements_resolver=MagicMock(return_value=None),
        bridge_factory=MagicMock(),
        mcp_supervisor_factory=MagicMock(),
        heartbeat_policy_from_env_fn=MagicMock(),
        check_mcp_bridge_health_fn=MagicMock(),
        policy_bundle=None,
        policy_bundle_factory=None,
        state_factory=None,
        recovery_controller_factory=None,
        marker_watcher_factory=None,
        snapshot_registry=SnapshotRegistry(),
        recovery_sleep=lambda _seconds: None,
    )

    assert deps.display_context is display_context
    prompt_path = deps.master_prompt_materializer(Path("/workspace"), "commit")
    assert prompt_path == ".agent/tmp/master_prompt_commit.md"
    assert workspace.read(".agent/tmp/master_prompt_commit.md") == "fake master prompt"
    assert deps.bridge_factory is not None


def test_pipeline_deps_fake_registry_factory_return_value() -> None:
    """``registry_factory`` can return a fake registry that satisfies the
    ``get(name)`` protocol used by ``execute_agent_effect``.
    """
    agent_config = AgentConfig(cmd="fake-agent")

    class FakeRegistry:
        def get(self, name: str) -> AgentConfig | None:
            if name == "fake-agent":
                return agent_config
            return None

    display_context = _fake_display_context()
    deps = PipelineDeps(
        display_context=display_context,
        registry_factory=lambda _config: FakeRegistry(),
    )

    assert deps.registry_factory is not None
    registry = deps.registry_factory(UnifiedConfig())
    assert isinstance(registry, FakeRegistry)
    assert registry.get("fake-agent") is agent_config
    assert registry.get("missing") is None


def test_pipeline_deps_fake_agent_executor_seam() -> None:
    """``PipelineDeps`` composes with ``FakeAgentExecutor`` to replay agent
    output into a ``MemoryWorkspace`` without subprocesses or real I/O.

    This exercises the required fake executor seam end-to-end: the registry
    factory resolves an agent config, and a seeded ``FakeAgentExecutor``
    drives the agent boundary deterministically.
    """
    workspace = MemoryWorkspace()
    agent_config = AgentConfig(
        cmd="fake-agent",
        json_parser=JsonParserType.GENERIC,
        transport=AgentTransport.GENERIC,
    )

    class FakeRegistry:
        def get(self, name: str) -> AgentConfig | None:
            return agent_config if name == "fake-agent" else None

    deps = PipelineDeps(
        display_context=_fake_display_context(),
        registry_factory=lambda _config: FakeRegistry(),
        master_prompt_materializer=MagicMock(return_value=".agent/system.md"),
        phase_prompt_materializer=MagicMock(return_value=".agent/phase.md"),
        bridge_factory=MagicMock(),
    )

    registry = deps.registry_factory(UnifiedConfig())
    resolved = registry.get("fake-agent")
    assert resolved is agent_config

    unit = WorkUnit(
        unit_id="fake-agent",
        description="fake agent work",
        allowed_directories=["tmp"],
    )
    executor = FakeAgentExecutor(
        runs={
            "fake-agent": FakeRun(
                outputs=["line one", "line two"],
                exit_code=0,
                duration_ms=1,
            )
        }
    )

    async def _run() -> WorkerResult:
        return await executor.run(
            unit,
            on_output=lambda line: workspace.append("output.txt", f"{line}\n"),
            on_status=lambda _status: None,
        )

    result = asyncio.run(_run())
    assert result.exit_code == 0
    assert executor.calls == [unit]
    assert workspace.read("output.txt") == "line one\nline two\n"
