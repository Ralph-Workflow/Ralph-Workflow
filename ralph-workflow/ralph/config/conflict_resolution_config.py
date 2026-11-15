from __future__ import annotations

from pydantic import ConfigDict, Field, model_validator

from ralph.config.gitlink_policy_config import GitlinkPolicyConfig
from ralph.pydantic_compat import RalphBaseModel

_REMOVED_SETTING_GUIDANCE: dict[str, str] = {
    "max_consecutive_resolver_attempts": (
        "Use 'agent_max_consecutive_unproductive' and 'agent_max_total_unproductive' "
        "in [general] instead."
    ),
    "inactivity_timeout_seconds": "Use 'general.agent_idle_timeout_seconds' instead.",
    "status_interval_seconds": "Remove this setting; resolver-specific status reporting is retired.",
}


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
            present = sorted(_REMOVED_SETTING_GUIDANCE.keys() & data.keys())
            if present:
                guidance = " ".join(_REMOVED_SETTING_GUIDANCE[key] for key in present)
                raise ValueError(
                    f"Configuration setting(s) {present} have been removed. {guidance}"
                )
        return data


__all__ = ["ConflictResolutionConfig"]
