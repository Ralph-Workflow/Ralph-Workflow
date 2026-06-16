"""CapabilitySet — lightweight set of Ralph capabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.protocol.capability_mapping import Capability as RalphCapability
from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.mcp.session_plan import default_prompt_capability_identifiers

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


class CapabilitySet:
    """Lightweight set of Ralph capabilities."""

    def __init__(self, values: Iterable[RalphCapability] | None = None) -> None:
        self._values = frozenset(values or ())

    def contains(self, capability: RalphCapability) -> bool:
        return capability in self._values

    def insert(self, capability: RalphCapability) -> None:
        self._values = frozenset((*self._values, capability))

    def __iter__(self) -> Iterator[RalphCapability]:
        return iter(self._values)

    def iter(self) -> Iterable[RalphCapability]:
        return iter(self._values)

    def to_vec(self) -> tuple[RalphCapability, ...]:
        return tuple(self._values)

    @classmethod
    def defaults_for_drain(cls, drain: SessionDrain) -> CapabilitySet:
        return cls(_default_capabilities_for_drain(drain))

    @classmethod
    def from_identifiers(cls, identifiers: Iterable[str] | None) -> CapabilitySet:
        if not identifiers:
            return cls()
        values: list[RalphCapability] = []
        for identifier in identifiers:
            try:
                values.append(RalphCapability(identifier))
            except ValueError:
                continue
        return cls(values)


def _default_capabilities_for_drain(drain: SessionDrain) -> tuple[RalphCapability, ...]:
    return tuple(
        RalphCapability(identifier) for identifier in default_prompt_capability_identifiers(drain)
    )


DEFAULT_CAPABILITIES: dict[SessionDrain, tuple[RalphCapability, ...]] = {
    drain: _default_capabilities_for_drain(drain) for drain in SessionDrain
}


__all__ = ["DEFAULT_CAPABILITIES", "CapabilitySet"]
