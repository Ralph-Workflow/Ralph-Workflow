"""Capability health state models.

Defines the six-state capability lifecycle model and the four-field health state
for all dependency-backed baseline helpers (web_search, visit_url, docs_mcp, skills).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


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

    status: CapabilityStatus = Field(default=CapabilityStatus.NOT_INSTALLED)
    last_check_ok_iso: str = ""  # ISO 8601; empty if never checked successfully
    last_check_fail_iso: str = ""  # ISO 8601; empty if never failed
    update_available: bool = False
    ralph_version: str = ""  # Ralph version that wrote this entry; for OUTDATED detection


class CapabilityState(RalphBaseModel):
    """Capability health state for all dependency-backed helpers."""

    model_config = ConfigDict(frozen=True)

    web_search: CapabilityEntry = Field(
        default_factory=lambda: CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED)
    )
    visit_url: CapabilityEntry = Field(
        default_factory=lambda: CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED)
    )
    docs_mcp: CapabilityEntry = Field(
        default_factory=lambda: CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED)
    )
    skills: CapabilityEntry = Field(
        default_factory=lambda: CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED)
    )


__all__ = ["CapabilityEntry", "CapabilityState", "CapabilityStatus"]
