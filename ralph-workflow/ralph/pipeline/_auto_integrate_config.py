from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig


def configured_target(config: UnifiedConfig) -> str:
    try:
        configured: object = object.__getattribute__(config.general, "auto_integrate_target")
    except AttributeError:
        configured = ""
    return configured if isinstance(configured, str) else ""


def missing_target_reason(config: UnifiedConfig) -> str:
    target = configured_target(config)
    return (
        f"local integration target branch does not exist: {target}"
        if target
        else "no integration target configured"
    )
