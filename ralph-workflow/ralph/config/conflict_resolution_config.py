from __future__ import annotations

from pydantic import ConfigDict, Field, model_validator

from ralph.config.gitlink_policy_config import GitlinkPolicyConfig
from ralph.pydantic_compat import RalphBaseModel


class ConflictResolutionConfig(RalphBaseModel):
    model_config = ConfigDict(frozen=True)

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
                "status_interval_seconds",
                "inactivity_timeout_seconds",
            }
            present = removed_keys.intersection(data.keys())
            if present:
                sorted_keys = sorted(present)
                if "max_consecutive_resolver_attempts" in present:
                    raise ValueError(
                        f"Configuration setting(s) {sorted_keys} have been removed. "
                        "Use 'agent_max_consecutive_unproductive' and "
                        "'agent_max_total_unproductive' in [general] instead."
                    )
                raise ValueError(
                    f"Configuration setting(s) {sorted_keys} have been removed. "
                    "Use 'agent_idle_timeout_seconds' and "
                    "'agent_waiting_status_interval_seconds' in [general] instead."
                )
        return data


__all__ = ["ConflictResolutionConfig"]
