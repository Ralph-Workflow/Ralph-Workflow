"""Pro DI seam: ``ProPipelineHooks`` frozen dataclass.

Pro can inject custom pipeline collaborators into the run loop
via ``ProPipelineHooks``. The dataclass bundles 13 fields:

- 5 factory callables that, when supplied, REPLACE the
  corresponding runner helpers:

    - ``policy_bundle_factory``: ``(WorkspaceScope, UnifiedConfig)
      -> PolicyBundle``
    - ``registry_factory``: ``(UnifiedConfig) -> AgentRegistry``
    - ``state_factory``: ``(UnifiedConfig, AgentsPolicy,
      PipelinePolicy, dict[str, int] | None) -> PipelineState``
    - ``recovery_controller_factory``: ``(PipelineState,
      PolicyBundle, UnifiedConfig) -> tuple[RecoveryController,
      int]``
    - ``marker_watcher_factory``: ``(Path) -> ProMarkerWatcher``

- 1 override: ``policy_bundle_override: PolicyBundle | None``;
  when set, the line ``policy_bundle = factory(workspace_scope,
  config)`` is replaced with
  ``policy_bundle = pro_hooks.policy_bundle_override``.

- 1 passthrough: ``snapshot_registry: SnapshotRegistry | None``;
  when set, the inner loop publishes a ``PipelineStateSnapshot``
  to this registry on each reduce step.

- 6 collaborator overrides that are applied to ``PipelineDeps``
  by ``build_default_pipeline_deps``:

    - ``display_context``: overrides the display context.
    - ``model_identity``: overrides the multimodal model identity.
    - ``system_prompt_materializer``: overrides the system-prompt
      materializer.
    - ``phase_prompt_materializer``: overrides the phase-prompt
      materializer.
    - ``artifact_requirements_resolver``: overrides the artifact-
      requirements resolver.
    - ``recovery_sleep``: overrides the wall-clock sleep used
      during recovery backoff.

Invariant: every field is keyword-only with a default of
``None``; the dataclass is ``frozen=True, slots=True`` so it
cannot be mutated after construction.
"""

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
        MaterializeSystemPromptFn,
        PhasePromptMaterializerFn,
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
    """DI seam: lets Pro inject custom pipeline collaborators into ``run()``.

    All fields default to ``None``. When a factory is ``None``,
    ``run()`` uses the production helper (the existing behaviour).
    When ``policy_bundle_override`` is not ``None``,
    ``policy_bundle_factory`` is short-circuited.

    The six collaborator overrides (``display_context``,
    ``model_identity``, ``system_prompt_materializer``,
    ``phase_prompt_materializer``, ``artifact_requirements_resolver``,
    ``recovery_sleep``) are applied to the ``PipelineDeps`` built by
    ``build_default_pipeline_deps`` so Pro can inject custom
    implementations of the primary pipeline collaborators
    (display, model, prompt, artifact requirements, recovery sleep)
    without changing the shared execution core.
    """

    policy_bundle_factory: PolicyBundleFactory | None = None
    registry_factory: RegistryFactory | None = None
    state_factory: StateFactory | None = None
    recovery_controller_factory: RecoveryControllerFactory | None = None
    marker_watcher_factory: MarkerWatcherFactory | None = None
    policy_bundle_override: PolicyBundle | None = None
    snapshot_registry: SnapshotRegistry | None = None
    display_context: DisplayContext | None = None
    model_identity: MultimodalModelIdentity | None = None
    system_prompt_materializer: MaterializeSystemPromptFn | None = None
    phase_prompt_materializer: PhasePromptMaterializerFn | None = None
    artifact_requirements_resolver: ArtifactRequirementsResolverFn | None = None
    recovery_sleep: Callable[[float], None] | None = None

    def to_runner_kwargs(self) -> dict[str, object]:
        """Return the 6 kwargs to forward to ``run()``.

        The 6 collaborator overrides (including ``recovery_sleep``)
        are intentionally NOT included here because they are not
        ``run()`` kwargs; they are fields that ``build_default_pipeline_deps``
        inspects separately when composing ``PipelineDeps``.
        ``policy_bundle_override`` is also intentionally excluded because
        it is not a ``run()`` kwarg; it is a field that ``run()`` inspects
        separately to short-circuit ``policy_bundle_factory``.
        """
        return {
            "policy_bundle_factory": self.policy_bundle_factory,
            "registry_factory": self.registry_factory,
            "state_factory": self.state_factory,
            "recovery_controller_factory": self.recovery_controller_factory,
            "marker_watcher_factory": self.marker_watcher_factory,
            "snapshot_registry": self.snapshot_registry,
        }


__all__ = ["ProPipelineHooks"]
