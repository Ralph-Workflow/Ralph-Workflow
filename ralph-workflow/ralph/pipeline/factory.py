"""Pipeline dependency bundle and factory.

This module is the single composition point for the five PROMPT-mandated
collaborators (display, model identity, system/phase prompt materializers,
artifact resolver), the bridge factory, and all seven ``ProPipelineHooks`` overrides.
Both the main pipeline (via ``run_loop.run``) and plumbing commands compose
from :class:`PipelineCore` so they share the same underlying collaborators.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

import ralph.pipeline.session_bridge as _session_bridge
from ralph.mcp.protocol.startup import heartbeat_policy_from_env
from ralph.mcp.server.lifecycle import check_mcp_bridge_health
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pro_support.hooks import apply_pro_hooks_to_core
from ralph.process.mcp_supervisor import McpSupervisor
from ralph.prompts.materialize import materialize_prompt_for_phase
from ralph.prompts.system_prompt import materialize_system_prompt
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager
    from datetime import timedelta
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
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
    from ralph.recovery.connectivity import ConnectivityEvent, ConnectivityState
    from ralph.workspace.scope import WorkspaceScope


class MaterializeSystemPromptFn(Protocol):
    """Materialize a system prompt file and return its path.

    Matches the wrapper in ``ralph._session_runtime_deps`` and the public
    ``ralph.prompts.system_prompt.materialize_system_prompt`` surface.
    """

    def __call__(
        self,
        workspace_root: Path,
        name: str,
        default_current_prompt: str | None = None,
        worker_namespace: Path | None = None,
    ) -> str: ...


class PhasePromptMaterializerFn(Protocol):
    """Materialize a phase prompt and return its dump path."""

    def __call__(
        self,
        context: PromptPhaseContext | None = None,
        options: PromptPhaseOptions | None = None,
        **kwargs: object,
    ) -> str: ...


class ArtifactRequirementsResolverFn(Protocol):
    """Resolve the required artifact contract for a phase/drain."""

    def __call__(
        self,
        pipeline_policy: PipelinePolicy,
        artifacts_policy: ArtifactsPolicy,
        *,
        phase: str,
        drain: str | None = None,
    ) -> RequiredArtifact | None: ...


class ConnectivityMonitorLike(Protocol):
    """Connectivity state source used by the pipeline run loop."""

    @property
    def current_state(self) -> ConnectivityState: ...

    def add_listener(
        self, cb: Callable[[ConnectivityEvent], None]
    ) -> Callable[[], None]: ...


class McpSupervisorFactoryFn(Protocol):
    """Construct a context manager that supervises an MCP bridge during invocation."""

    def __call__(
        self,
        bridge: RestartAwareMcpBridge,
        *,
        check_interval: timedelta,
        on_restart: Callable[[int], None] | None,
    ) -> AbstractContextManager[object, bool | None]: ...


class HeartbeatPolicyFromEnvFn(Protocol):
    """Return the heartbeat policy read from the environment."""

    def __call__(self) -> HeartbeatPolicy: ...


class CheckMcpBridgeHealthFn(Protocol):
    """Check that an MCP bridge is healthy, raising on failure."""

    def __call__(self, bridge: SessionBridgeLike) -> None: ...


def _materialize_system_prompt(
    workspace_root: Path,
    name: str,
    default_current_prompt: str | None = None,
    worker_namespace: Path | None = None,
) -> str:
    return materialize_system_prompt(
        workspace_root=workspace_root,
        name=name,
        default_current_prompt=default_current_prompt,
        worker_namespace=worker_namespace,
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
) -> AbstractContextManager[object, bool | None]:
    return cast(
        "AbstractContextManager[object, bool | None]",
        McpSupervisor(
            bridge,
            check_interval=check_interval,
            on_restart=on_restart,
        ),
    )


def _heartbeat_policy_from_env() -> HeartbeatPolicy:
    return heartbeat_policy_from_env()


def _check_mcp_bridge_health(bridge: SessionBridgeLike) -> None:
    check_mcp_bridge_health(bridge)


@dataclasses.dataclass(frozen=True, slots=True)
class PipelineCore:
    """The five PROMPT-mandated pipeline collaborators.

    This is the lean, modular surface shared by the main pipeline and
    plumbing commands. It contains exactly the collaborators that can be
    injected by Pro via :class:`ralph.pro_support.hooks.ProPipelineHooks`:

    - ``display_context`` — display/rendering context.
    - ``model_identity`` — resolved multimodal model identity.
    - ``system_prompt_materializer`` — system-prompt materializer.
    - ``phase_prompt_materializer`` — phase-prompt materializer.
    - ``artifact_requirements_resolver`` — phase/drain artifact resolver.

    The bridge factory is intentionally NOT part of ``PipelineCore``; it is
    a plumbing-only concern and is supplied separately to plumbing call sites.
    """

    display_context: DisplayContext
    model_identity: MultimodalModelIdentity | None = None
    system_prompt_materializer: MaterializeSystemPromptFn = _materialize_system_prompt
    phase_prompt_materializer: PhasePromptMaterializerFn = _materialize_prompt_for_phase
    artifact_requirements_resolver: ArtifactRequirementsResolverFn = (
        _resolve_phase_required_artifact
    )


_UNSET: object = object()


@dataclasses.dataclass(frozen=True, slots=True, init=False)
class PipelineDeps:
    """Injectable dependency bundle for the pipeline and plumbing commands.

    Fields cover the five PROMPT-mandated collaborators (bundled in
    ``core``), the bridge factory, MCP lifecycle machinery, the
    seven ``ProPipelineHooks`` overrides, and the recovery sleep seam.

    For backward compatibility, the four collaborators may still be passed
    directly to ``__init__``; they are composed into the embedded
    :class:`PipelineCore`. Callers that already have a ``PipelineCore``
    should pass ``core=...`` instead.
    """

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
    marker_watcher_factory: MarkerWatcherFactory | None = None
    snapshot_registry: SnapshotRegistry | None = None
    recovery_sleep: Callable[[float], None] | None = None
    # Live runtime signals from the pipeline that the watchdog consults
    # on every evaluate() call so the StuckClassifier gate can return
    # DUPLICATE_KILL (when the pipeline is already in a wait state) or
    # WAITING_ON_CONNECTIVITY (when the network is offline) and defer
    # the fire. Both providers are optional; the watchdog falls back
    # to "no live signal" when they are None. The run loop populates
    # these from its live PipelineState and ConnectivityMonitor.
    connectivity_state_provider: Callable[[], str | None] | None = None
    is_waiting_state_provider: Callable[[], bool] | None = None
    # Session-wide process teardown invoked from the run-loop's
    # cleanup finally. Defaults to ``ProcessManager.shutdown_all``
    # when unset so non-phase-labeled children (invoke:/agent:)
    # are reaped on every exit (normal, error, SIGINT, SIGTERM).
    # Wrapped in ``suppress(Exception)`` at the call site so a
    # refusing-to-die child cannot break the suite.
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

    def __init__(
        self,
        *,
        core: PipelineCore | None = None,
        display_context: DisplayContext | object = _UNSET,
        model_identity: MultimodalModelIdentity | None | object = _UNSET,
        system_prompt_materializer: MaterializeSystemPromptFn | object = _UNSET,
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
            Callable[[UnifiedConfig, WorkspaceScope, RebaseState], RebaseState | None]
            | None
        ) = None,
        commit_effect_executor: Callable[[object, Path], object] | None = None,
        has_uncommitted_changes: Callable[[Path], bool] | None = None,
    ) -> None:
        core_overrides: dict[str, object] = {}
        if display_context is not _UNSET:
            core_overrides["display_context"] = display_context
        if model_identity is not _UNSET:
            core_overrides["model_identity"] = model_identity
        if system_prompt_materializer is not _UNSET:
            core_overrides["system_prompt_materializer"] = system_prompt_materializer
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
                system_prompt_materializer=(
                    _materialize_system_prompt
                    if system_prompt_materializer is _UNSET
                    else cast("MaterializeSystemPromptFn", system_prompt_materializer)
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
            if "system_prompt_materializer" in core_overrides:
                effective_core = dataclasses.replace(
                    effective_core,
                    system_prompt_materializer=cast(
                        "MaterializeSystemPromptFn",
                        core_overrides["system_prompt_materializer"],
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

    @property
    def display_context(self) -> DisplayContext:
        """Backward-compatible accessor for ``core.display_context``."""
        return self.core.display_context

    @property
    def model_identity(self) -> MultimodalModelIdentity | None:
        """Backward-compatible accessor for ``core.model_identity``."""
        return self.core.model_identity

    @property
    def system_prompt_materializer(self) -> MaterializeSystemPromptFn:
        """Backward-compatible accessor for ``core.system_prompt_materializer``."""
        return self.core.system_prompt_materializer

    @property
    def phase_prompt_materializer(self) -> PhasePromptMaterializerFn:
        """Backward-compatible accessor for ``core.phase_prompt_materializer``."""
        return self.core.phase_prompt_materializer

    @property
    def artifact_requirements_resolver(self) -> ArtifactRequirementsResolverFn:
        """Backward-compatible accessor for ``core.artifact_requirements_resolver``."""
        return self.core.artifact_requirements_resolver


@runtime_checkable
class PipelineFactory(Protocol):
    """Factory that builds a ``PipelineDeps`` for a given config."""

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
    """Build the shared 4-collaborator ``PipelineCore``.

    This is the lean composition root used by both plumbing commands and
    ``build_default_pipeline_deps``. It does NOT accept ``pro_hooks``,
    ``policy_bundle``, ``recovery_sleep``, or any other extended field;
    callers that need the extended bundle should use
    :func:`build_default_pipeline_deps`.
    """
    del config
    return PipelineCore(
        display_context=display_context,
        model_identity=model_identity,
    )


def _resolve_policy_bundle(
    config: UnifiedConfig,
    pro_hooks: ProPipelineHooks | None,
) -> tuple[PolicyBundle | None, PolicyBundleFactory | None]:
    """Resolve policy_bundle with the same 3-way priority as ``run_loop.run``.

    Returns ``(policy_bundle, policy_bundle_factory)``. When an override or
    factory resolves, ``policy_bundle_factory`` is None because the bundle is
    already resolved at build time.
    """
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
    """Return a new ``PipelineDeps`` with ``ProPipelineHooks`` overrides applied."""
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
    """Build a ``PipelineDeps`` wired to production defaults.

    ``model_identity`` defaults to ``None`` so callers that do not have a
    resolved identity reproduce the pre-refactor ``UNKNOWN_IDENTITY`` behavior;
    callers that already know the effective identity (e.g. plumbing commands
    with a single selected agent) can inject it here.

    ``policy_bundle`` lets the main pipeline load the policy once and inject
    it into the shared bundle instead of passing it as a separate runner
    argument.

    ``recovery_sleep`` lets callers replace the wall-clock sleep used during
    recovery backoff; ``pro_hooks.recovery_sleep`` takes precedence over this
    argument when both are provided.
    """
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
    """Default composition root for the main pipeline and plumbing commands.

    This thin, stateless factory implements :class:`PipelineFactory` by
    delegating to :func:`build_default_pipeline_deps`. Because the extended
    call sites (main pipeline, parallel worker runtime) need to inject
    ``model_identity`` and ``policy_bundle``, the :meth:`build` method accepts
    those optional kwargs in addition to the Protocol surface.
    """

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
        """Build a :class:`PipelineDeps` wired to production defaults."""
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
    "MaterializeSystemPromptFn",
    "McpSupervisorFactoryFn",
    "PhasePromptMaterializerFn",
    "PipelineCore",
    "PipelineDeps",
    "PipelineFactory",
    "apply_pro_hooks_to_deps",
    "build_default_pipeline_deps",
    "build_minimal_pipeline_core",
]
