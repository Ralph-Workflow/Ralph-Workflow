"""_ExecutePipelineRequest — parameters for executing the pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.enums import Verbosity
    from ralph.config.models import UnifiedConfig
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PolicyBundle
    from ralph.pro_support.hooks import ProPipelineHooks


class _ExecutePipelineRequest(NamedTuple):
    config: UnifiedConfig
    initial_state: PipelineState | None
    policy_bundle: PolicyBundle | None
    verbosity: Verbosity | None
    counter_overrides: dict[str, int]
    config_path: Path | None = None
    cli_overrides: dict[str, object] | None = None
    parallel_worker_manifest: Path | None = None
    pro_hooks: ProPipelineHooks | None = None
    model_identity: MultimodalModelIdentity | None = None


__all__ = ["_ExecutePipelineRequest"]
