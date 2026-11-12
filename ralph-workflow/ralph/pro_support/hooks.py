from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.pipeline.factory import (
        ArtifactRequirementsResolverFn,
        MaterializeMasterPromptFn,
        PhasePromptMaterializerFn,
        PipelineCore,
    )
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import AgentsPolicy, PipelinePolicy, PolicyBundle
    from ralph.pro_support.state_query import SnapshotRegistry
    from ralph.pro_support.watcher import ProMarkerWatcher
    from ralph.recovery.controller import RecoveryController
    from ralph.workspace.scope import WorkspaceScope

    PolicyBundleFactory = Callable[["WorkspaceScope", "UnifiedConfig"], PolicyBundle]
    RegistryFactory = Callable[["UnifiedConfig"], object]
    StateFactory = Callable[
        ["UnifiedConfig", "AgentsPolicy", "PipelinePolicy", dict[str, int] | None],
        "PipelineState",
    ]
    RecoveryControllerFactory = Callable[
        ["PipelineState", "PolicyBundle", "UnifiedConfig"],
        tuple["RecoveryController", int],
    ]
    MarkerWatcherFactory = Callable[["Path"], "ProMarkerWatcher"]


@dataclasses.dataclass(frozen=True, slots=True)
class ProPipelineHooks:
    policy_bundle_factory: PolicyBundleFactory | None = None
    registry_factory: RegistryFactory | None = None
    state_factory: StateFactory | None = None
    recovery_controller_factory: RecoveryControllerFactory | None = None
    marker_watcher_factory: MarkerWatcherFactory | None = None
    policy_bundle_override: PolicyBundle | None = None
    snapshot_registry: SnapshotRegistry | None = None
    display_context: DisplayContext | None = None
    model_identity: MultimodalModelIdentity | None = None
    master_prompt_materializer: MaterializeMasterPromptFn | None = None
    phase_prompt_materializer: PhasePromptMaterializerFn | None = None
    artifact_requirements_resolver: ArtifactRequirementsResolverFn | None = None
    recovery_sleep: Callable[[float], None] | None = None

    def to_runner_kwargs(self) -> dict[str, object]:
        return {
            "policy_bundle_factory": self.policy_bundle_factory,
            "registry_factory": self.registry_factory,
            "state_factory": self.state_factory,
            "recovery_controller_factory": self.recovery_controller_factory,
            "marker_watcher_factory": self.marker_watcher_factory,
            "snapshot_registry": self.snapshot_registry,
        }


def apply_pro_hooks_to_core(
    core: PipelineCore,
    pro_hooks: ProPipelineHooks,
) -> PipelineCore:
    if pro_hooks.display_context is not None:
        core = dataclasses.replace(core, display_context=pro_hooks.display_context)
    if pro_hooks.model_identity is not None:
        core = dataclasses.replace(core, model_identity=pro_hooks.model_identity)
    if pro_hooks.master_prompt_materializer is not None:
        core = dataclasses.replace(
            core, master_prompt_materializer=pro_hooks.master_prompt_materializer
        )
    if pro_hooks.phase_prompt_materializer is not None:
        core = dataclasses.replace(
            core, phase_prompt_materializer=pro_hooks.phase_prompt_materializer
        )
    if pro_hooks.artifact_requirements_resolver is not None:
        core = dataclasses.replace(
            core, artifact_requirements_resolver=pro_hooks.artifact_requirements_resolver
        )
    return core


__all__ = ["ProPipelineHooks", "apply_pro_hooks_to_core"]
