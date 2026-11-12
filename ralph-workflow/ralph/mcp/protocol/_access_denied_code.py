from __future__ import annotations

from enum import StrEnum


class AccessDeniedCode(StrEnum):
    NOT_INITIALIZED = "NotInitialized"
    CAPABILITY_DENIED = "CapabilityDenied"
    READ_ONLY_MODE = "ReadOnlyMode"
    OUTSIDE_ROOT_DIR = "OutsideRootDir"
    TOOL_NOT_ALLOWED = "ToolNotAllowed"


__all__ = ["AccessDeniedCode"]
