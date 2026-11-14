from __future__ import annotations

from enum import StrEnum

from pydantic import ConfigDict

from ralph.pydantic_compat import RalphBaseModel


class GitlinkPreference(StrEnum):
    OURS = "ours"
    THEIRS = "theirs"
    VENDOR_IMMUTABLE = "vendor-immutable"


class GitlinkPolicyConfig(RalphBaseModel):
    model_config = ConfigDict(frozen=True)

    pointer_preference: GitlinkPreference = GitlinkPreference.VENDOR_IMMUTABLE


__all__ = ["GitlinkPolicyConfig", "GitlinkPreference"]
