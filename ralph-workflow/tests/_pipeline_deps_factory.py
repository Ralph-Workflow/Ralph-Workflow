"""Test helpers for constructing ``PipelineDeps`` with fakes."""

from __future__ import annotations

import dataclasses
import uuid
from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.protocol.startup import HeartbeatPolicy
from ralph.mcp.session_plan import SessionModelOpts, build_session_mcp_plan
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import (
    ArtifactRequirementsResolverFn,
    CheckMcpBridgeHealthFn,
    HeartbeatPolicyFromEnvFn,
    MaterializeSystemPromptFn,
    McpSupervisorFactoryFn,
    PhasePromptMaterializerFn,
    PipelineDeps,
)
from ralph.recovery.testing import FakeConnectivityMonitor

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.config.enums import AgentTransport
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.pipeline.session_bridge import BridgeFactory
    from ralph.policy.models import AgentsPolicy, ArtifactsPolicy, PipelinePolicy, PolicyBundle
    from ralph.pro_support.hooks import (
        MarkerWatcherFactory,
        PolicyBundleFactory,
        RecoveryControllerFactory,
        StateFactory,
    )
    from ralph.pro_support.state_query import SnapshotRegistry
    from ralph.prompts.materialize import PromptPhaseContext, PromptPhaseOptions


class _FakeBridge:
    """Minimal stand-in for a ``SessionBridgeLike`` in tests."""

    def __init__(self) -> None:
        self.run_id = str(uuid.uuid4())

    def shutdown(self) -> None:
        pass

    def agent_endpoint_uri(self) -> str:
        return "http://127.0.0.1:12345/mcp"

    def reset_tool_registry(self) -> None:
        pass


class _RecordingBridgeFactory:
    """Bridge factory that records every call and returns a configured bridge."""

    def __init__(self, bridge: object | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._bridge = bridge

    def __call__(
        self,
        *,
        workspace_root: Path,
        drain: str,
        agents_policy: AgentsPolicy | None,
        transport: AgentTransport | None = None,
        capabilities: frozenset[str] | None = None,
        session_id_prefix: str | None = None,
        run_id: str | None = None,
        model_identity: MultimodalModelIdentity | None = None,
        parallel_worker: bool = False,
        worker_namespace: Path | None = None,
        worker_artifact_dir: Path | None = None,
        allowed_roots: tuple[Path, ...] | None = None,
    ) -> object:
        effective_capabilities = (
            capabilities
            if capabilities is not None
            else frozenset(
                build_session_mcp_plan(
                    transport=transport,
                    drain=drain,
                    workspace_path=workspace_root,
                    agents_policy=agents_policy,
                    model_opts=SessionModelOpts(model_identity=model_identity)
                    if model_identity is not None
                    else None,
                    model_flag=None,
                ).capabilities
            )
        )
        self.calls.append(
            {
                "workspace_root": workspace_root,
                "drain": drain,
                "agents_policy": agents_policy,
                "transport": transport,
                "capabilities": effective_capabilities,
                "session_id_prefix": session_id_prefix,
                "run_id": run_id,
                "model_identity": model_identity,
                "parallel_worker": parallel_worker,
                "worker_namespace": worker_namespace,
                "worker_artifact_dir": worker_artifact_dir,
                "allowed_roots": allowed_roots,
            }
        )
        if self._bridge is not None:
            return self._bridge
        return _FakeBridge()


def _artifact_requirements_resolver_impl(
    _pipeline_policy: PipelinePolicy,
    _artifacts_policy: ArtifactsPolicy,
    *,
    phase: str,
    drain: str | None = None,
) -> RequiredArtifact | None:
    del _pipeline_policy, _artifacts_policy, phase, drain
    return None


_artifact_requirements_resolver: ArtifactRequirementsResolverFn = cast(
    "ArtifactRequirementsResolverFn", _artifact_requirements_resolver_impl
)


def _phase_prompt_materializer_impl(
    _context: PromptPhaseContext | None = None,
    _options: PromptPhaseOptions | None = None,
    **kwargs: object,
) -> str:
    worker_namespace = kwargs.get("worker_namespace")
    if worker_namespace is not None:
        path = Path(str(worker_namespace)) / "tmp" / "development_prompt.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("worker prompt body", encoding="utf-8")
        return str(path)
    return ".agent/tmp/development_prompt.md"


_phase_prompt_materializer: PhasePromptMaterializerFn = cast(
    "PhasePromptMaterializerFn", _phase_prompt_materializer_impl
)


def _system_prompt_materializer_impl(
    workspace_root: Path,
    name: str,
    default_current_prompt: str | None = None,
    worker_namespace: Path | None = None,
) -> str:
    del default_current_prompt
    root = worker_namespace if worker_namespace is not None else workspace_root
    path = Path(root) / ".agent" / "tmp" / f"system_prompt_{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("system prompt", encoding="utf-8")
    return str(path)


_system_prompt_materializer: MaterializeSystemPromptFn = _system_prompt_materializer_impl


def _mcp_supervisor_factory(
    bridge: object,
    *,
    check_interval: object,
    on_restart: object,
) -> object:
    del bridge, check_interval, on_restart
    return nullcontext()


def _heartbeat_policy_from_env() -> object:
    return HeartbeatPolicy(interval=timedelta(seconds=2))


def _check_mcp_bridge_health(_bridge: object) -> None:
    return


def make_test_pipeline_deps(
    display_context: DisplayContext,
    *,
    bridge: object | None = None,
    bridge_factory: BridgeFactory | None = None,
    system_prompt_materializer: MaterializeSystemPromptFn | None = None,
    phase_prompt_materializer: PhasePromptMaterializerFn | None = None,
    artifact_requirements_resolver: ArtifactRequirementsResolverFn | None = None,
    registry_factory: Callable[[UnifiedConfig], object] | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    mcp_supervisor_factory: McpSupervisorFactoryFn | None = None,
    heartbeat_policy_from_env_fn: HeartbeatPolicyFromEnvFn | None = None,
    check_mcp_bridge_health_fn: CheckMcpBridgeHealthFn | None = None,
    policy_bundle: PolicyBundle | None = None,
    policy_bundle_factory: PolicyBundleFactory | None = None,
    state_factory: StateFactory | None = None,
    recovery_controller_factory: RecoveryControllerFactory | None = None,
    marker_watcher_factory: MarkerWatcherFactory | None = None,
    snapshot_registry: SnapshotRegistry | None = None,
    process_teardown: Callable[[], None] | None = None,
) -> PipelineDeps:
    """Build a ``PipelineDeps`` suitable for fast, deterministic tests."""
    deps = PipelineDeps(
        display_context=display_context,
        model_identity=model_identity,
        registry_factory=registry_factory,
        system_prompt_materializer=(system_prompt_materializer or _system_prompt_materializer),
        phase_prompt_materializer=(phase_prompt_materializer or _phase_prompt_materializer),
        artifact_requirements_resolver=(
            artifact_requirements_resolver or _artifact_requirements_resolver
        ),
        bridge_factory=cast(
            "BridgeFactory",
            bridge_factory or _RecordingBridgeFactory(bridge),
        ),
        mcp_supervisor_factory=mcp_supervisor_factory or _mcp_supervisor_factory,
        heartbeat_policy_from_env_fn=heartbeat_policy_from_env_fn or _heartbeat_policy_from_env,
        check_mcp_bridge_health_fn=check_mcp_bridge_health_fn or _check_mcp_bridge_health,
        connectivity_monitor=FakeConnectivityMonitor(),
        catchup_worker_factory=lambda _config, _workspace_root: None,
        startup_rebase_resolver=lambda _config, _workspace_scope: None,
        auto_integrate_resolver=lambda _config, _workspace_scope, _rebase: None,
        commit_effect_executor=lambda _effect, _workspace_root: PipelineEvent.COMMIT_SKIPPED,
        has_uncommitted_changes=lambda _workspace_root: True,
        process_teardown=process_teardown or (lambda: None),
    )
    if policy_bundle is not None:
        deps = dataclasses.replace(deps, policy_bundle=policy_bundle)
    if policy_bundle_factory is not None:
        deps = dataclasses.replace(deps, policy_bundle_factory=policy_bundle_factory)
    if state_factory is not None:
        deps = dataclasses.replace(deps, state_factory=state_factory)
    if recovery_controller_factory is not None:
        deps = dataclasses.replace(deps, recovery_controller_factory=recovery_controller_factory)
    if marker_watcher_factory is not None:
        deps = dataclasses.replace(deps, marker_watcher_factory=marker_watcher_factory)
    if snapshot_registry is not None:
        deps = dataclasses.replace(deps, snapshot_registry=snapshot_registry)
    return deps


def make_recording_bridge_factory(bridge: object | None = None) -> _RecordingBridgeFactory:
    """Return a bridge factory that records call arguments."""
    return _RecordingBridgeFactory(bridge)
