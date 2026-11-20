from __future__ import annotations

from typing import TYPE_CHECKING

from ralph._typed_attrs import read_attr, read_float_attr
from ralph.config.general_config import LAST_RESORT_WAIT_SECONDS

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


def configured_last_resort_wait_seconds(config: UnifiedConfig) -> float:
    general: object = read_attr(config, "general", config)
    return read_float_attr(
        general, "auto_integrate_last_resort_wait_seconds", LAST_RESORT_WAIT_SECONDS
    )
