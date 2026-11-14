from __future__ import annotations

from enum import StrEnum


class GitlinkPreference(StrEnum):
    OURS = "ours"
    THEIRS = "theirs"
    VENDOR_IMMUTABLE = "vendor-immutable"


__all__ = ["GitlinkPreference"]
