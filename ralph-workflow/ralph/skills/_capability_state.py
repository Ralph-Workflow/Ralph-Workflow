"""Capability state model."""

from __future__ import annotations

from pydantic import Field

from ralph.pydantic_compat import ConfigDict, RalphBaseModel
from ralph.skills._capability_entry import CapabilityEntry


class CapabilityState(RalphBaseModel):
    """Capability health state for all dependency-backed helpers."""

    model_config = ConfigDict(frozen=True)

    web_search: CapabilityEntry = Field(default_factory=CapabilityEntry)
    visit_url: CapabilityEntry = Field(default_factory=CapabilityEntry)
    docs_mcp: CapabilityEntry = Field(default_factory=CapabilityEntry)
    skills: CapabilityEntry = Field(default_factory=CapabilityEntry)
