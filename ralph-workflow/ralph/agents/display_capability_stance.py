from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.agents.display_capabilities import (
    _ALL_DISPLAY_CAPABILITIES,
    DisplayCapability,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class DisplayCapabilityStance:
    capability: DisplayCapability
    kind: str
    reason: str = ""

    def __post_init__(self) -> None:
        if self.kind not in _STANCE_KINDS:
            msg = f"DisplayCapabilityStance.kind must be one of {_STANCE_KINDS}, got {self.kind!r}"
            raise ValueError(msg)
        if self.kind != "supported" and (not self.reason or not self.reason.strip()):
            msg = (
                f"{self.kind} stance for {self.capability.name!r} requires "
                f"a non-empty reason; an unreferenced reason is exactly "
                f"the silence the tri-state model exists to prevent"
            )
            raise ValueError(msg)
        if self.capability not in _ALL_DISPLAY_CAPABILITIES:
            msg = (
                f"Unknown display capability {self.capability!r}; "
                f"supported capabilities are "
                f"{[c.name for c in _ALL_DISPLAY_CAPABILITIES]}"
            )
            raise ValueError(msg)

    @classmethod
    def supported(
        cls, capability: DisplayCapability, *, detail: str = ""
    ) -> DisplayCapabilityStance:
        return cls(capability=capability, kind="supported", reason=detail)

    @classmethod
    def not_applicable(cls, capability: DisplayCapability, reason: str) -> DisplayCapabilityStance:
        stripped = reason.strip()
        if not stripped:
            msg = "NOT_APPLICABLE reason must be a non-empty string"
            raise ValueError(msg)
        return cls(capability=capability, kind="not_applicable", reason=stripped)

    @classmethod
    def unimplemented(cls, capability: DisplayCapability, reason: str) -> DisplayCapabilityStance:
        stripped = reason.strip()
        if not stripped:
            msg = "UNIMPLEMENTED reason must be a non-empty string"
            raise ValueError(msg)
        return cls(capability=capability, kind="unimplemented", reason=stripped)

    @property
    def is_supported(self) -> bool:
        return self.kind == "supported"

    def label(self) -> str:
        if self.kind == "supported":
            return "SUPPORTED"
        return f"{_STANCE_KIND_TO_LABEL[self.kind]} ({self.reason})"


_STANCE_KINDS: frozenset[str] = frozenset({"supported", "not_applicable", "unimplemented"})
_STANCE_KIND_TO_LABEL: Mapping[str, str] = {
    "supported": "SUPPORTED",
    "not_applicable": "NOT_APPLICABLE",
    "unimplemented": "UNIMPLEMENTED",
}


SUPPORTED: str = "supported"
NOT_APPLICABLE: str = "not_applicable"
UNIMPLEMENTED: str = "unimplemented"


__all__ = [
    "NOT_APPLICABLE",
    "SUPPORTED",
    "UNIMPLEMENTED",
    "DisplayCapabilityStance",
]
