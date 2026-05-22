"""Capability health state models - canonical definitions.

These classes are the authoritative source for capability state tracking.
All consumers must import from this module.
"""

from __future__ annotations

from enum import StrEnum

from pydantic import Field

from ralph.pydantic_compat import ConfigDict, RalphBaseModel


class CapabilityStatus(StrEnum):
    """Six-state capability lifecycle."""

    NOT_INSTALLED = "not_installed"
    INSTALLED_HEALTHY = "installed_healthy"
    CONFIGURED_UNREACHABLE = "configured_unreachable"
    INSTALLED_OUTDATED = "installed_outdated"
    INSTALLED_DEGRADED = "installed_degraded"
    NEEDS_REPAIR = "needs_repair"


class CapabilityEntry(RalphBaseModel):
    """Health entry for a single capability."""

    model_config = ConfigDict(frozen=True)

    status: CapabilityStatus = CapabilityStatus.NOT_INSTALLED
    last_check_ok_iso: str = ""  # ISO 8601; empty if never checked successfully
    last_check_fail_iso: str = ""  # ISO 8601; empty if never failed
    update_available: bool = False
    ralph_version: str = ""  # Ralph version that wrote this entry; used to detect INSTALLED_OUTDATED


class CapabilityState(RalphBaseModel):
    """Capability health state for all dependency-backed helpers."""

    model_config = ConfigDict(frozen=True)

    web_search: CapabilityEntry = Field(default_factory=CapabilityEntry)
    visit_url: CapabilityEntry = Field(default_factory=CapabilityEntry)
    docs_mcp: CapabilityEntry = Field(default_factory=CapabilityEntry)
    skills: CapabilityEntry = Field(default_factory=CapabilityEntry)


__all__ = ["CapabilityEntry", "CapabilityState", "CapabilityStatus"]
