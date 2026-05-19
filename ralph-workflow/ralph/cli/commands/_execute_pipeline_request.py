"""_ExecutePipelineRequest — parameters for executing the pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from ralph.config.enums import Verbosity
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.state import PipelineState


class _ExecutePipelineRequest(NamedTuple):
    config: UnifiedConfig
    initial_state: PipelineState | None
    policy_bundle: object
    verbosity: Verbosity | None
    counter_overrides: dict[str, int]


__all__ = ["_ExecutePipelineRequest"]
