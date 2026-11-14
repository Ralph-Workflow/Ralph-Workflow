from __future__ import annotations

import math

from pydantic import ConfigDict, Field, field_validator, model_validator

from ralph.config.gitlink_policy_config import GitlinkPolicyConfig
from ralph.pydantic_compat import RalphBaseModel


class ConflictResolutionConfig(RalphBaseModel):
    model_config = ConfigDict(frozen=True)

    inactivity_timeout_seconds: float = Field(default=900.0, gt=0.0)
    status_interval_seconds: float = Field(default=30.0, gt=0.0)
    max_rounds_per_stop: int = Field(
        default=3,
        ge=1,
        description=(
            "Maximum number of resolution rounds one conflict-resolution stop runs. "
            "A round that reaches the cap without resolving the conflict escalates "
            "to intervention instead of invoking the agent again."
        ),
    )
    max_rebase_conflict_stops: int = Field(default=10, ge=1)
    gitlink_policy: GitlinkPolicyConfig = Field(default_factory=GitlinkPolicyConfig)
    max_fallback_agents: int = Field(
        default=2,
        ge=1,
        description=(
            "Compatibility field retained for older configs. It does not cap "
            "how many chain candidates conflict resolution tries; the bound "
            "agent chain alone decides candidate breadth."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_removed_resolver_settings(cls, data: object) -> object:
        if isinstance(data, dict):
            removed_keys = {
                "max_consecutive_resolver_attempts",
            }
            present = removed_keys.intersection(data.keys())
            if present:
                sorted_keys = sorted(present)
                raise ValueError(
                    f"Configuration setting(s) {sorted_keys} have been removed. "
                    "Use 'agent_max_consecutive_unproductive' and 'agent_max_total_unproductive' in [general] instead."
                )
        return data

    @field_validator(
        "inactivity_timeout_seconds", "status_interval_seconds"
    )
    @classmethod
    def _validate_finite_duration(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("must be finite")
        return value

__all__ = ["ConflictResolutionConfig"]
