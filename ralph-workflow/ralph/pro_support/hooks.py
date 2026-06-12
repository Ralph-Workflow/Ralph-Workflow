"""Pro DI seam: ``ProPipelineHooks`` frozen dataclass.

Pro can inject custom pipeline collaborators into the run loop
via ``ProPipelineHooks``. The dataclass bundles 7 fields:

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
    """

    policy_bundle_factory: PolicyBundleFactory | None = None
    registry_factory: RegistryFactory | None = None
    state_factory: StateFactory | None = None
    recovery_controller_factory: RecoveryControllerFactory | None = None
    marker_watcher_factory: MarkerWatcherFactory | None = None
    policy_bundle_override: PolicyBundle | None = None
    snapshot_registry: SnapshotRegistry | None = None

    def to_runner_kwargs(self) -> dict[str, object]:
        """Return the 6 kwargs to forward to ``run()``.

        ``policy_bundle_override`` is intentionally NOT included
        here because it is not a ``run()`` kwarg; it is a field
        that ``run()`` inspects separately to short-circuit
        ``policy_bundle_factory``.
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
