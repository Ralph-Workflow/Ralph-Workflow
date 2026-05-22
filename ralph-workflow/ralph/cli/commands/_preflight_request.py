"""_PreflightRequest — parameters for all preflight validation checks."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.state import PipelineState
    from ralph.workspace.scope import WorkspaceScope


class _PreflightRequest(NamedTuple):
    config: UnifiedConfig
    workspace_scope: WorkspaceScope | None
    policy_bundle: object
    initial_state: PipelineState | None
    counter_overrides: dict[str, int]
    inline_prompt: str | None = None
    parallel_worker_manifest: Path | None = None


__all__ = ["_PreflightRequest"]
