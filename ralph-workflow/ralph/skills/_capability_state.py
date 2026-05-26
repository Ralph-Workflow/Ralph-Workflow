"""Capability state model."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel
from ralph.skills._capability_entry import CapabilityEntry
from ralph.skills._capability_status import CapabilityStatus


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


__all__ = ["CapabilityState"]
