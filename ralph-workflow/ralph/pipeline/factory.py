from __future__ import annotations

import dataclasses
import time
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

import ralph.pipeline.session_bridge as _session_bridge
from ralph.agents.invoke import invoke_agent as _default_invoke_agent
from ralph.mcp.protocol.startup import heartbeat_policy_from_env
from ralph.mcp.server.lifecycle import check_mcp_bridge_health
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pro_support.hooks import apply_pro_hooks_to_core
from ralph.process.mcp_supervisor import McpSupervisor
from ralph.prompts.master_prompt import materialize_master_prompt
from ralph.prompts.materialize import materialize_prompt_for_phase
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from contextlib import AbstractContextManager
    from datetime import timedelta
    from pathlib import Path

    from ralph.agents.clock import Clock
    from ralph.agents.invoke._types import InvokeOptions
    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.mcp.protocol.startup import HeartbeatPolicy
    from ralph.mcp.server.lifecycle import RestartAwareMcpBridge, SessionBridgeLike
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.pipeline.auto_integrate_catchup import AutoIntegrateCatchupWorker
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.policy.models import ArtifactsPolicy, PipelinePolicy, PolicyBundle
    from ralph.pro_support.hooks import (
        MarkerWatcherFactory,
        PolicyBundleFactory,
        ProPipelineHooks,
        RecoveryControllerFactory,
        StateFactory,
    )
    from ralph.pro_support.state_query import SnapshotRegistry
    from ralph.prompts.materialize import PromptPhaseContext, PromptPhaseOptions
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger
    from ralph.recovery.agent_unavailability_tracker import UnavailabilityStore
    from ralph.recovery.connectivity import ConnectivityEvent, ConnectivityState
    from ralph.recovery.controller import RecoveryController
    from ralph.workspace.scope import WorkspaceScope


class MaterializeMasterPromptFn(Protocol):
    def __call__(
        self,
        workspace_root: Path,
        name: str,
        default_product_criteria: str | None = None,
        worker_namespace: Path | None = None,
        planning_style: bool = False,
        transport: AgentTransport | None = None,
    ) -> str: ...


class PhasePromptMaterializerFn(Protocol):
    def __call__(
        self,
        context: PromptPhaseContext | None = None,
        options: PromptPhaseOptions | None = None,
        **kwargs: object,
    ) -> str: ...


class ArtifactRequirementsResolverFn(Protocol):
    def __call__(
        self,
        pipeline_policy: PipelinePolicy,
        artifacts_policy: ArtifactsPolicy,
        *,
        phase: str,
        drain: str | None = None,
    ) -> RequiredArtifact | None: ...


class InvokeAgentFn(Protocol):
    def __call__(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterable[object]: ...


class ConnectivityMonitorLike(Protocol):
    @property
    def current_state(self) -> ConnectivityState: ...

    def add_listener(self, cb: Callable[[ConnectivityEvent], None]) -> Callable[[], None]: ...


class McpSupervisorFactoryFn(Protocol):
    def __call__(
        self,
        bridge: RestartAwareMcpBridge,
        *,
        check_interval: timedelta,
        on_restart: Callable[[int], None] | None,
        agent_label_scope: str,
    ) -> AbstractContextManager[object, bool | None]: ...


class HeartbeatPolicyFromEnvFn(Protocol):
    def __call__(self) -> HeartbeatPolicy: ...


class CheckMcpBridgeHealthFn(Protocol):
    def __call__(self, bridge: SessionBridgeLike) -> None: ...


def _materialize_master_prompt(
    workspace_root: Path,
    name: str,
    default_product_criteria: str | None = None,
    worker_namespace: Path | None = None,
    planning_style: bool = False,
    transport: AgentTransport | None = None,
) -> str:
    return materialize_master_prompt(
        workspace_root=workspace_root,
        name=name,
        default_product_criteria=default_product_criteria,
        worker_namespace=worker_namespace,
        planning_style=planning_style,
        transport=transport,
    )


def _materialize_prompt_for_phase(
    context: PromptPhaseContext | None = None,
    options: PromptPhaseOptions | None = None,
    **kwargs: object,
) -> str:
    return materialize_prompt_for_phase(context, options, **kwargs)


def _resolve_phase_required_artifact(
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy,
    *,
    phase: str,
    drain: str | None = None,
) -> RequiredArtifact | None:
    return resolve_phase_required_artifact(
        pipeline_policy,
        artifacts_policy,
        phase=phase,
        drain=drain,
    )


def _mcp_supervisor_factory(
    bridge: RestartAwareMcpBridge,
    *,
    check_interval: timedelta,
    on_restart: Callable[[int], None] | None,
    agent_label_scope: str,
) -> AbstractContextManager[object, bool | None]:
    return cast(
        "AbstractContextManager[object, bool | None]",
        McpSupervisor(
            bridge,
            check_interval=check_interval,
            on_restart=on_restart,
            agent_label_scope=agent_label_scope,
        ),
    )


def _heartbeat_policy_from_env() -> HeartbeatPolicy:
    return heartbeat_policy_from_env()


def _check_mcp_bridge_health(bridge: SessionBridgeLike) -> None:
    check_mcp_bridge_health(bridge)


@dataclasses.dataclass(frozen=True, slots=True)
class PipelineCore:
    display_context: DisplayContext
    model_identity: MultimodalModelIdentity | None = None
    master_prompt_materializer: MaterializeMasterPromptFn = _materialize_master_prompt
    phase_prompt_materializer: PhasePromptMaterializerFn = _materialize_prompt_for_phase
    artifact_requirements_resolver: ArtifactRequirementsResolverFn = (
        _resolve_phase_required_artifact
    )


_UNSET: object = object()


def _assign_startup_seam_fields(
    deps: PipelineDeps,
    *,
    resolve_workspace_scope_fn: Callable[[Path | str | None], WorkspaceScope] | None,
    write_start_commit_fn: Callable[[Path], None] | None,
    validate_custom_mcp_servers_fn: Callable[[Path], int] | None,
    recovery_clock: Clock | None,
) -> None:
    object.__setattr__(deps, "resolve_workspace_scope_fn", resolve_workspace_scope_fn)
    object.__setattr__(deps, "write_start_commit_fn", write_start_commit_fn)
    object.__setattr__(deps, "validate_custom_mcp_servers_fn", validate_custom_mcp_servers_fn)
    object.__setattr__(deps, "recovery_clock", recovery_clock)


@dataclasses.dataclass(frozen=True, init=False)
class PipelineDeps:
    core: PipelineCore
    registry_factory: Callable[[UnifiedConfig], object] | None = None
    bridge_factory: _session_bridge.BridgeFactory = _session_bridge.build_session_bridge
    mcp_supervisor_factory: McpSupervisorFactoryFn = _mcp_supervisor_factory
    heartbeat_policy_from_env_fn: HeartbeatPolicyFromEnvFn = _heartbeat_policy_from_env
    check_mcp_bridge_health_fn: CheckMcpBridgeHealthFn = _check_mcp_bridge_health
    policy_bundle: PolicyBundle | None = None
    policy_bundle_factory: PolicyBundleFactory | None = None
    state_factory: StateFactory | None = None
    recovery_controller_factory: RecoveryControllerFactory | None = None
    unavailability_store: UnavailabilityStore | None = None
    ledger: AgentFailureLedger | None = None
    recovery_controller: RecoveryController | None = None
    marker_watcher_factory: MarkerWatcherFactory | None = None
    snapshot_registry: SnapshotRegistry | None = None
    recovery_sleep: Callable[[float], None] | None = None
    connectivity_state_provider: Callable[[], str | None] | None = None
    is_waiting_state_provider: Callable[[], bool] | None = None
    process_teardown: Callable[[], None] | None = None
    connectivity_monitor: ConnectivityMonitorLike | None = None
    catchup_worker_factory: (
        Callable[[UnifiedConfig, Path], AutoIntegrateCatchupWorker | None] | None
    ) = None
    startup_rebase_resolver: (
        Callable[[UnifiedConfig, WorkspaceScope], RebaseState | None] | None
    ) = None
    auto_integrate_resolver: (
        Callable[[UnifiedConfig, WorkspaceScope, RebaseState], RebaseState | None] | None
    ) = None
    commit_effect_executor: Callable[[object, Path], object] | None = None
    has_uncommitted_changes: Callable[[Path], bool] | None = None
    invoke_agent: InvokeAgentFn = _default_invoke_agent
    monotonic: Callable[[], float] = time.monotonic
    wall_time: Callable[[], float] = time.time
    resolve_workspace_scope_fn: Callable[[Path | str | None], WorkspaceScope] | None = None
    write_start_commit_fn: Callable[[Path], None] | None = None
    validate_custom_mcp_servers_fn: Callable[[Path], int] | None = None
    recovery_clock: Clock | None = None

    def __init__(
        self,
        *,
        core: PipelineCore | None = None,
        display_context: DisplayContext | object = _UNSET,
        model_identity: MultimodalModelIdentity | None | object = _UNSET,
        master_prompt_materializer: MaterializeMasterPromptFn | object = _UNSET,
        phase_prompt_materializer: PhasePromptMaterializerFn | object = _UNSET,
        artifact_requirements_resolver: ArtifactRequirementsResolverFn | object = _UNSET,
        registry_factory: Callable[[UnifiedConfig], object] | None = None,
        bridge_factory: _session_bridge.BridgeFactory = _session_bridge.build_session_bridge,
        mcp_supervisor_factory: McpSupervisorFactoryFn = _mcp_supervisor_factory,
        heartbeat_policy_from_env_fn: HeartbeatPolicyFromEnvFn = _heartbeat_policy_from_env,
        check_mcp_bridge_health_fn: CheckMcpBridgeHealthFn = _check_mcp_bridge_health,
        policy_bundle: PolicyBundle | None = None,
        policy_bundle_factory: PolicyBundleFactory | None = None,
        state_factory: StateFactory | None = None,
        recovery_controller_factory: RecoveryControllerFactory | None = None,
        unavailability_store: UnavailabilityStore | None = None,
        ledger: AgentFailureLedger | None = None,
        recovery_controller: RecoveryController | None = None,
        marker_watcher_factory: MarkerWatcherFactory | None = None,
        snapshot_registry: SnapshotRegistry | None = None,
        recovery_sleep: Callable[[float], None] | None = None,
        connectivity_state_provider: Callable[[], str | None] | None = None,
        is_waiting_state_provider: Callable[[], bool] | None = None,
        process_teardown: Callable[[], None] | None = None,
        connectivity_monitor: ConnectivityMonitorLike | None = None,
        catchup_worker_factory: (
            Callable[[UnifiedConfig, Path], AutoIntegrateCatchupWorker | None] | None
        ) = None,
        startup_rebase_resolver: (
            Callable[[UnifiedConfig, WorkspaceScope], RebaseState | None] | None
        ) = None,
        auto_integrate_resolver: (
            Callable[[UnifiedConfig, WorkspaceScope, RebaseState], RebaseState | None] | None
        ) = None,
        commit_effect_executor: Callable[[object, Path], object] | None = None,
        has_uncommitted_changes: Callable[[Path], bool] | None = None,
        invoke_agent: InvokeAgentFn = _default_invoke_agent,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
        resolve_workspace_scope_fn: Callable[[Path | str | None], WorkspaceScope] | None = None,
        write_start_commit_fn: Callable[[Path], None] | None = None,
        validate_custom_mcp_servers_fn: Callable[[Path], int] | None = None,
        recovery_clock: Clock | None = None,
    ) -> None:
        core_overrides: dict[str, object] = {}
        if display_context is not _UNSET:
            core_overrides["display_context"] = display_context
        if model_identity is not _UNSET:
            core_overrides["model_identity"] = model_identity
        if master_prompt_materializer is not _UNSET:
            core_overrides["master_prompt_materializer"] = master_prompt_materializer
        if phase_prompt_materializer is not _UNSET:
            core_overrides["phase_prompt_materializer"] = phase_prompt_materializer
        if artifact_requirements_resolver is not _UNSET:
            core_overrides["artifact_requirements_resolver"] = artifact_requirements_resolver

        if core is None:
            if display_context is _UNSET:
                raise ValueError("display_context is required when core is not provided")
            effective_core = PipelineCore(
                display_context=cast("DisplayContext", display_context),
                model_identity=(
                    None
                    if model_identity is _UNSET
                    else cast("MultimodalModelIdentity | None", model_identity)
                ),
                master_prompt_materializer=(
                    _materialize_master_prompt
                    if master_prompt_materializer is _UNSET
                    else cast("MaterializeMasterPromptFn", master_prompt_materializer)
                ),
                phase_prompt_materializer=(
                    _materialize_prompt_for_phase
                    if phase_prompt_materializer is _UNSET
                    else cast("PhasePromptMaterializerFn", phase_prompt_materializer)
                ),
                artifact_requirements_resolver=(
                    _resolve_phase_required_artifact
                    if artifact_requirements_resolver is _UNSET
                    else cast(
                        "ArtifactRequirementsResolverFn",
                        artifact_requirements_resolver,
                    )
                ),
            )
        elif core_overrides:
            effective_core = core
            if "display_context" in core_overrides:
                effective_core = dataclasses.replace(
                    effective_core,
                    display_context=cast("DisplayContext", core_overrides["display_context"]),
                )
            if "model_identity" in core_overrides:
                effective_core = dataclasses.replace(
                    effective_core,
                    model_identity=cast(
                        "MultimodalModelIdentity | None", core_overrides["model_identity"]
                    ),
                )
            if "master_prompt_materializer" in core_overrides:
                effective_core = dataclasses.replace(
                    effective_core,
                    master_prompt_materializer=cast(
                        "MaterializeMasterPromptFn",
                        core_overrides["master_prompt_materializer"],
                    ),
                )
            if "phase_prompt_materializer" in core_overrides:
                effective_core = dataclasses.replace(
                    effective_core,
                    phase_prompt_materializer=cast(
                        "PhasePromptMaterializerFn",
                        core_overrides["phase_prompt_materializer"],
                    ),
                )
            if "artifact_requirements_resolver" in core_overrides:
                effective_core = dataclasses.replace(
                    effective_core,
                    artifact_requirements_resolver=cast(
                        "ArtifactRequirementsResolverFn",
                        core_overrides["artifact_requirements_resolver"],
                    ),
                )
        else:
            effective_core = core

        object.__setattr__(self, "core", effective_core)
        object.__setattr__(self, "registry_factory", registry_factory)
        object.__setattr__(self, "bridge_factory", bridge_factory)
        object.__setattr__(self, "mcp_supervisor_factory", mcp_supervisor_factory)
        object.__setattr__(self, "heartbeat_policy_from_env_fn", heartbeat_policy_from_env_fn)
        object.__setattr__(self, "check_mcp_bridge_health_fn", check_mcp_bridge_health_fn)
        object.__setattr__(self, "policy_bundle", policy_bundle)
        object.__setattr__(self, "policy_bundle_factory", policy_bundle_factory)
        object.__setattr__(self, "state_factory", state_factory)
        object.__setattr__(self, "recovery_controller_factory", recovery_controller_factory)
        object.__setattr__(self, "unavailability_store", unavailability_store)
        object.__setattr__(self, "ledger", ledger)
        object.__setattr__(self, "recovery_controller", recovery_controller)
        object.__setattr__(self, "marker_watcher_factory", marker_watcher_factory)
        object.__setattr__(self, "snapshot_registry", snapshot_registry)
        object.__setattr__(self, "recovery_sleep", recovery_sleep)
        object.__setattr__(self, "connectivity_state_provider", connectivity_state_provider)
        object.__setattr__(self, "is_waiting_state_provider", is_waiting_state_provider)
        object.__setattr__(self, "process_teardown", process_teardown)
        object.__setattr__(self, "connectivity_monitor", connectivity_monitor)
        object.__setattr__(self, "catchup_worker_factory", catchup_worker_factory)
        object.__setattr__(self, "startup_rebase_resolver", startup_rebase_resolver)
        object.__setattr__(self, "auto_integrate_resolver", auto_integrate_resolver)
        object.__setattr__(self, "commit_effect_executor", commit_effect_executor)
        object.__setattr__(self, "has_uncommitted_changes", has_uncommitted_changes)
        object.__setattr__(self, "invoke_agent", invoke_agent)
        object.__setattr__(self, "monotonic", monotonic)
        object.__setattr__(self, "wall_time", wall_time)
        _assign_startup_seam_fields(
            self,
            resolve_workspace_scope_fn=resolve_workspace_scope_fn,
            write_start_commit_fn=write_start_commit_fn,
            validate_custom_mcp_servers_fn=validate_custom_mcp_servers_fn,
            recovery_clock=recovery_clock,
        )

    @property
    def display_context(self) -> DisplayContext:
        return self.core.display_context

    @property
    def model_identity(self) -> MultimodalModelIdentity | None:
        return self.core.model_identity

    @property
    def master_prompt_materializer(self) -> MaterializeMasterPromptFn:
        return self.core.master_prompt_materializer

    @property
    def phase_prompt_materializer(self) -> PhasePromptMaterializerFn:
        return self.core.phase_prompt_materializer

    @property
    def artifact_requirements_resolver(self) -> ArtifactRequirementsResolverFn:
        return self.core.artifact_requirements_resolver


@runtime_checkable
class PipelineFactory(Protocol):
    def build(
        self,
        config: UnifiedConfig,
        display_context: DisplayContext,
        *,
        pro_hooks: ProPipelineHooks | None = None,
    ) -> PipelineDeps: ...


def build_minimal_pipeline_core(
    config: UnifiedConfig,
    display_context: DisplayContext,
    *,
    model_identity: MultimodalModelIdentity | None = None,
) -> PipelineCore:
    del config
    return PipelineCore(
        display_context=display_context,
        model_identity=model_identity,
    )


def _resolve_policy_bundle(
    config: UnifiedConfig,
    pro_hooks: ProPipelineHooks | None,
) -> tuple[PolicyBundle | None, PolicyBundleFactory | None]:
    if pro_hooks is not None and pro_hooks.policy_bundle_override is not None:
        return pro_hooks.policy_bundle_override, None

    if pro_hooks is not None and pro_hooks.policy_bundle_factory is not None:
        workspace_scope = resolve_workspace_scope()
        return pro_hooks.policy_bundle_factory(workspace_scope, config), None

    return None, None


def apply_pro_hooks_to_deps(
    deps: PipelineDeps,
    pro_hooks: ProPipelineHooks,
    config: UnifiedConfig,
) -> PipelineDeps:
    policy_bundle, _ = _resolve_policy_bundle(config, pro_hooks)
    if policy_bundle is not None:
        deps = dataclasses.replace(deps, policy_bundle=policy_bundle)
    if pro_hooks.policy_bundle_factory is not None:
        deps = dataclasses.replace(deps, policy_bundle_factory=pro_hooks.policy_bundle_factory)
    if pro_hooks.registry_factory is not None:
        deps = dataclasses.replace(deps, registry_factory=pro_hooks.registry_factory)
    if pro_hooks.state_factory is not None:
        deps = dataclasses.replace(deps, state_factory=pro_hooks.state_factory)
    if pro_hooks.recovery_controller_factory is not None:
        deps = dataclasses.replace(
            deps, recovery_controller_factory=pro_hooks.recovery_controller_factory
        )
    if pro_hooks.marker_watcher_factory is not None:
        deps = dataclasses.replace(deps, marker_watcher_factory=pro_hooks.marker_watcher_factory)
    if pro_hooks.snapshot_registry is not None:
        deps = dataclasses.replace(deps, snapshot_registry=pro_hooks.snapshot_registry)
    if pro_hooks.recovery_sleep is not None:
        deps = dataclasses.replace(deps, recovery_sleep=pro_hooks.recovery_sleep)
    core = apply_pro_hooks_to_core(deps.core, pro_hooks)
    if core is not deps.core:
        deps = dataclasses.replace(deps, core=core)
    return deps


def build_default_pipeline_deps(
    config: UnifiedConfig,
    display_context: DisplayContext,
    *,
    model_identity: MultimodalModelIdentity | None = None,
    policy_bundle: PolicyBundle | None = None,
    recovery_sleep: Callable[[float], None] | None = None,
    pro_hooks: ProPipelineHooks | None = None,
) -> PipelineDeps:
    core = build_minimal_pipeline_core(
        config,
        display_context,
        model_identity=model_identity,
    )
    deps = PipelineDeps(
        core=core,
        policy_bundle=policy_bundle,
        recovery_sleep=recovery_sleep,
    )
    if pro_hooks is None:
        return deps
    return apply_pro_hooks_to_deps(deps, pro_hooks, config)


class DefaultPipelineFactory:
    def build(
        self,
        config: UnifiedConfig,
        display_context: DisplayContext,
        *,
        model_identity: MultimodalModelIdentity | None = None,
        policy_bundle: PolicyBundle | None = None,
        recovery_sleep: Callable[[float], None] | None = None,
        pro_hooks: ProPipelineHooks | None = None,
    ) -> PipelineDeps:
        return build_default_pipeline_deps(
            config,
            display_context,
            model_identity=model_identity,
            policy_bundle=policy_bundle,
            recovery_sleep=recovery_sleep,
            pro_hooks=pro_hooks,
        )


__all__ = [
    "ArtifactRequirementsResolverFn",
    "CheckMcpBridgeHealthFn",
    "DefaultPipelineFactory",
    "HeartbeatPolicyFromEnvFn",
    "MaterializeMasterPromptFn",
    "McpSupervisorFactoryFn",
    "PhasePromptMaterializerFn",
    "PipelineCore",
    "PipelineDeps",
    "PipelineFactory",
    "apply_pro_hooks_to_deps",
    "build_default_pipeline_deps",
    "build_minimal_pipeline_core",
]
