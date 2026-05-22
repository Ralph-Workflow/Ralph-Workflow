"""Capability status enum."""

from __future__ import annotations

from enum import StrEnum


class CapabilityStatus(StrEnum):
    """Six-state capability lifecycle."""

    NOT_INSTALLED = "not_installed"
    INSTALLED_HEALTHY = "installed_healthy"
    CONFIGURED_UNREACHABLE = "configured_unreachable"
    INSTALLED_OUTDATED = "installed_outdated"
    INSTALLED_DEGRADED = "installed_degraded"
    NEEDS_REPAIR = "needs_repair"
