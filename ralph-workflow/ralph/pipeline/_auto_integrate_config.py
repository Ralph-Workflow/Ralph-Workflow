"""Configured-target helpers for auto-integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig


def configured_target(config: UnifiedConfig) -> str:
    """Return the configured target unchanged for operator-facing skips."""
    configured: object = getattr(config.general, "auto_integrate_target", "")
    return configured if isinstance(configured, str) else ""


def missing_target_reason(config: UnifiedConfig) -> str:
    """Name the missing local target without guessing an alternative."""
    target = configured_target(config)
    return (
        f"local integration target branch does not exist: {target}"
        if target
        else "no integration target configured"
    )
