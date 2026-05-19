"""AccessDeniedCode — categorical access-denial codes."""

from __future__ import annotations

from enum import StrEnum


class AccessDeniedCode(StrEnum):
    """Categorical access-denial codes."""

    NOT_INITIALIZED = "NotInitialized"
    CAPABILITY_DENIED = "CapabilityDenied"
    READ_ONLY_MODE = "ReadOnlyMode"
    OUTSIDE_ROOT_DIR = "OutsideRootDir"
    TOOL_NOT_ALLOWED = "ToolNotAllowed"


__all__ = ["AccessDeniedCode"]
