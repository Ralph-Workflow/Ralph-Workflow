"""_PolicyPreflightRequest — parameters for policy-backed preflight checks."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PolicyBundle


class _PolicyPreflightRequest(NamedTuple):
    config: UnifiedConfig
    policy_bundle: PolicyBundle
    initial_state: PipelineState | None
    counter_overrides: dict[str, int]


__all__ = ["_PolicyPreflightRequest"]
