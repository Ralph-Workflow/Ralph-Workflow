"""Capability state model."""

from __future__ import annotations

from ralph.pydantic_compat import RalphBaseModel
from ralph.skills._capability_entry import CapabilityEntry


class CapabilityState(RalphBaseModel):
    """Capability health state for all dependency-backed helpers."""

    web_search: CapabilityEntry = CapabilityEntry()
    visit_url: CapabilityEntry = CapabilityEntry()
    docs_mcp: CapabilityEntry = CapabilityEntry()
    skills: CapabilityEntry = CapabilityEntry()
