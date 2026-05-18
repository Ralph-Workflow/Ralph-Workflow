"""_LoadResult — result of loading CLI configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope


class _LoadResult(NamedTuple):
    config: UnifiedConfig
    workspace_scope: WorkspaceScope | None
    initial_state: PipelineState | None
    policy_bundle: PolicyBundle | None


__all__ = ["_LoadResult"]
