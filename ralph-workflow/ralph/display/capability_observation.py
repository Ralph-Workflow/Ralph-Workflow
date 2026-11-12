from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.display_capabilities import DisplayCapability


@dataclass(frozen=True, slots=True)
class CapabilityObservation:
    capability: DisplayCapability
    tool_name: str
    unit_id: str


__all__ = ["CapabilityObservation"]
