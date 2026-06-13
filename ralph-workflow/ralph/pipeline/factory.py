"""Pipeline dependency bundle and factory.

This module is the single composition point for the four PROMPT-mandated
collaborators (display, model identity, prompt materializers, artifact
resolver), the bridge factory, and all seven ``ProPipelineHooks`` overrides.
Both the main pipeline (via ``run_loop.run``) and plumbing commands consume
``PipelineDeps`` so they share the same underlying collaborators.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import ralph.pipeline.session_bridge as _session_bridge
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.prompts.materialize import materialize_prompt_for_phase
from ralph.prompts.system_prompt import materialize_system_prompt
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.phases.required_artifacts import RequiredArtifact
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
    ) -> str:
        ...


class PhasePromptMaterializerFn(Protocol):
    """Materialize a phase prompt and return its dump path."""

    def __call__(
        self,
        context: PromptPhaseContext | None = None,
        options: PromptPhaseOptions | None = None,
        **kwargs: object,
    ) -> str:
        ...


class ArtifactRequirementsResolverFn(Protocol):
    """Resolve the required artifact contract for a phase/drain."""

    def __call__(
        self,
        pipeline_policy: PipelinePolicy,
        artifacts_policy: ArtifactsPolicy,
        *,
        phase: str,
        drain: str | None = None,
    ) -> RequiredArtifact | None:
        ...


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


@dataclasses.dataclass(frozen=True, slots=True)
class PipelineDeps:
    """Injectable dependency bundle for the pipeline and plumbing commands.

    Fields cover the four PROMPT-mandated collaborators
    (display_context, model_identity, system/phase prompt materializers,
    artifact resolver), the bridge factory, and the seven
    ``ProPipelineHooks`` overrides.
    """

    display_context: DisplayContext
    model_identity: MultimodalModelIdentity | None = None
    registry_factory: Callable[[UnifiedConfig], object] | None = None
    system_prompt_materializer: MaterializeSystemPromptFn = _materialize_system_prompt
    phase_prompt_materializer: PhasePromptMaterializerFn = _materialize_prompt_for_phase
    artifact_requirements_resolver: ArtifactRequirementsResolverFn = (
        _resolve_phase_required_artifact
    )
    bridge_factory: _session_bridge.BridgeFactory = _session_bridge.build_session_bridge
    policy_bundle: PolicyBundle | None = None
    policy_bundle_factory: PolicyBundleFactory | None = None
    state_factory: StateFactory | None = None
    recovery_controller_factory: RecoveryControllerFactory | None = None
    marker_watcher_factory: MarkerWatcherFactory | None = None
    snapshot_registry: SnapshotRegistry | None = None


@runtime_checkable
class PipelineFactory(Protocol):
    """Factory that builds a ``PipelineDeps`` for a given config."""

    def build(
        self,
        config: UnifiedConfig,
        display_context: DisplayContext,
        *,
        pro_hooks: ProPipelineHooks | None = None,
    ) -> PipelineDeps:
        ...


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
    return deps


def build_default_pipeline_deps(
    config: UnifiedConfig,
    display_context: DisplayContext,
    *,
    pro_hooks: ProPipelineHooks | None = None,
) -> PipelineDeps:
    """Build a ``PipelineDeps`` wired to production defaults.

    ``model_identity`` defaults to ``None`` so plumbing callers reproduce the
    pre-refactor ``UNKNOWN_IDENTITY`` behavior unless they explicitly inject a
    resolved identity.
    """
    deps = PipelineDeps(
        display_context=display_context,
        model_identity=None,
    )
    if pro_hooks is None:
        return deps
    return apply_pro_hooks_to_deps(deps, pro_hooks, config)


__all__ = [
    "ArtifactRequirementsResolverFn",
    "MaterializeSystemPromptFn",
    "PhasePromptMaterializerFn",
    "PipelineDeps",
    "PipelineFactory",
    "apply_pro_hooks_to_deps",
    "build_default_pipeline_deps",
]
