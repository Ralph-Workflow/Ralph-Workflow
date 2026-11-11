
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from rich.console import RenderableType

    from ralph.agents.display_capabilities import DisplayCapability
    from ralph.display.capability_observation import CapabilityObservation


_OBSERVATION_LOG_CAPACITY: int = 512


class CapabilityObservationRecorder:

    def __init__(self) -> None:
        self._observations: deque[CapabilityObservation] = deque(
            maxlen=_OBSERVATION_LOG_CAPACITY
        )
        self._observed_capabilities: set[DisplayCapability] = (
            set()
        )

    def record(self, observation: CapabilityObservation) -> None:
        self._observations.append(observation)
        self._observed_capabilities.add(observation.capability)

    def observed_capabilities(self) -> frozenset[DisplayCapability]:
        return frozenset(self._observed_capabilities)

    def observations_for_capability(
        self, capability: DisplayCapability
    ) -> tuple[CapabilityObservation, ...]:
        return tuple(o for o in self._observations if o.capability is capability)

    def observed_count(self) -> int:
        return len(self._observations)

    def clear(self) -> None:
        self._observations.clear()
        self._observed_capabilities.clear()

    def __iter__(self) -> Iterator[CapabilityObservation]:
        return iter(tuple(self._observations))


def capability_for_render(
    *,
    surface_name: str,
    tool_name: str | None,
) -> DisplayCapability | None:
    from ralph.agents.display_capabilities import (
        surface_to_capability,
    )

    return surface_to_capability(surface_name)


def infer_surface_for_preview(
    renderable: RenderableType | None,
    canonical_operation: str,
) -> str:
    del renderable
    if canonical_operation == "read":
        return "file_preview"
    if canonical_operation in {"write", "append", "NotebookEdit"}:
        return "syntax_preview"
    if canonical_operation in {"replace", "patch"}:
        return "diff_preview"
    return "syntax_preview"


__all__ = [
    "CapabilityObservationRecorder",
    "capability_for_render",
    "infer_surface_for_preview",
]
