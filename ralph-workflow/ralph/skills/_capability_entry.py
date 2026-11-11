
from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel
from ralph.skills._capability_status import CapabilityStatus


class CapabilityEntry(RalphBaseModel):

    model_config = ConfigDict(frozen=True)

    status: CapabilityStatus = Field(default=CapabilityStatus.NOT_INSTALLED)
    last_check_ok_iso: str = ""
    last_check_fail_iso: str = ""
    update_available: bool = False
    ralph_version: str = ""


__all__ = ["CapabilityEntry"]
