from __future__ import annotations

from pydantic import ConfigDict

from ralph.config.gitlink_preference import GitlinkPreference
from ralph.pydantic_compat import RalphBaseModel


class GitlinkPolicyConfig(RalphBaseModel):
    model_config = ConfigDict(frozen=True)

    pointer_preference: GitlinkPreference = GitlinkPreference.VENDOR_IMMUTABLE


__all__ = ["GitlinkPolicyConfig", "GitlinkPreference"]
