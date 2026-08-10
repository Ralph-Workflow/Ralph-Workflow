"""Tri-state ``DisplayCapabilityStance`` declaration.

Companion to :mod:`ralph.agents.display_capabilities`. The stance
type lives in its own module so the enum + helper-functions module
remains one public type per file (the repo-structure audit's
``multiple top-level classes`` rule). The stance is the runtime
structure the S-4 capability contract pins at every
:class:`ralph.agents.support.AgentSupport` registration; importing
it separately keeps the contract literal.
"""

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
    """A tri-state declaration of one capability for one agent.

    Three concrete factory constructors — :meth:`supported`,
    :meth:`not_applicable`, :meth:`unimplemented` — are the only
    supported construction paths. Each non-``SUPPORTED`` stance
    requires a non-empty reason; ``SUPPORTED`` accepts an optional
    free-form detail for diagnostic context (e.g. the fixture name
    that proves the stance) but does not require one. The two
    non-support stances are deliberately NOT interchangeable: a
    transport that physically cannot produce a file preview
    (``NOT_APPLICABLE``) is fundamentally different from one that
    could but currently does not (``UNIMPLEMENTED``), and the
    conformance matrix surfaces that distinction.
    """

    capability: DisplayCapability
    kind: str  # one of "supported", "not_applicable", "unimplemented"
    reason: str = ""

    def __post_init__(self) -> None:
        if self.kind not in _STANCE_KINDS:
            msg = (
                f"DisplayCapabilityStance.kind must be one of {_STANCE_KINDS}, "
                f"got {self.kind!r}"
            )
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
        """Declare a capability as ``SUPPORTED`` (optionally with a detail)."""
        return cls(capability=capability, kind="supported", reason=detail)

    @classmethod
    def not_applicable(
        cls, capability: DisplayCapability, reason: str
    ) -> DisplayCapabilityStance:
        """Declare a capability as ``NOT_APPLICABLE(reason)``.

        Use this when the transport is structurally incapable of
        producing the surface (e.g. an HTTP-only flow cannot ever
        surface a local file preview).
        """
        stripped = reason.strip()
        if not stripped:
            msg = "NOT_APPLICABLE reason must be a non-empty string"
            raise ValueError(msg)
        return cls(capability=capability, kind="not_applicable", reason=stripped)

    @classmethod
    def unimplemented(
        cls, capability: DisplayCapability, reason: str
    ) -> DisplayCapabilityStance:
        """Declare a capability as ``UNIMPLEMENTED(reason)``.

        Use this when the transport CAN produce the surface in
        principle, but the parser/display wiring has not yet been
        brought to the same shape as the working transports.
        """
        stripped = reason.strip()
        if not stripped:
            msg = "UNIMPLEMENTED reason must be a non-empty string"
            raise ValueError(msg)
        return cls(capability=capability, kind="unimplemented", reason=stripped)

    @property
    def is_supported(self) -> bool:
        """Return True iff this stance is ``SUPPORTED``."""
        return self.kind == "supported"

    def label(self) -> str:
        """Return the conformance-matrix label for this stance.

        ``SUPPORTED`` -> ``SUPPORTED``; ``NOT_APPLICABLE`` -> ``NOT_APPLICABLE (<reason>)``;
        ``UNIMPLEMENTED`` -> ``UNIMPLEMENTED (<reason>)``. The reason is inlined so an
        operator scanning the matrix sees both the kind and the explanation in one
        column.
        """
        if self.kind == "supported":
            return "SUPPORTED"
        return f"{_STANCE_KIND_TO_LABEL[self.kind]} ({self.reason})"


_STANCE_KINDS: frozenset[str] = frozenset({"supported", "not_applicable", "unimplemented"})
_STANCE_KIND_TO_LABEL: Mapping[str, str] = {
    "supported": "SUPPORTED",
    "not_applicable": "NOT_APPLICABLE",
    "unimplemented": "UNIMPLEMENTED",
}


#: Sentinel markers used by tests to demonstrate the tri-state
#: shape without coupling the test fixtures to specific
#: ``reason`` strings.
SUPPORTED: str = "supported"
NOT_APPLICABLE: str = "not_applicable"
UNIMPLEMENTED: str = "unimplemented"


__all__ = [
    "NOT_APPLICABLE",
    "SUPPORTED",
    "UNIMPLEMENTED",
    "DisplayCapabilityStance",
]
