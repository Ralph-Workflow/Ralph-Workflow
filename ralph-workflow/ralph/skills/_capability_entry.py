"""Capability entry model."""

from __future__ import annotations

from ralph.pydantic_compat import ConfigDict, RalphBaseModel
from ralph.skills._capability_status import CapabilityStatus


class CapabilityEntry(RalphBaseModel):
    """Health entry for a single capability."""

    model_config = ConfigDict(frozen=True)

    status: CapabilityStatus = CapabilityStatus.NOT_INSTALLED
    last_check_ok_iso: str = ""  # ISO 8601; empty if never checked successfully
    last_check_fail_iso: str = ""  # ISO 8601; empty if never failed
    update_available: bool = False
    ralph_version: str = ""  # Ralph version that wrote this entry
