"""Capability observation value object.

The full display-capability observation seam lives in
:mod:`ralph.display.capability_observation_recorder`. This module
exposes the small transport-neutral value object
:class:`CapabilityObservation` that the recorder stores per event.
Splitting the value object into its own module keeps the
capability_observation_recorder module to one public class per
file (the repo-structure audit's ``multiple top-level classes``
rule).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.display_capabilities import DisplayCapability


@dataclass(frozen=True, slots=True)
class CapabilityObservation:
    """One transport-neutral record of a capability that materialized.

    Attributes:
        capability: The capability that was rendered.
        tool_name: The bare tool name whose input triggered the render
            (e.g. ``"write"`` / ``"edit"`` / ``"read"``). Used as the
            diagnostic anchor in the conformance-matrix report so an
            operator can trace the render back to the specific tool
            call that exercised it.
        unit_id: The pipeline unit (agent invocation) that produced the
            observation. Mirrors the ``unit_id`` carried by
            :class:`ralph.display.parallel_display.ParallelDisplay`
            so the recorder can be queried per-unit when grading.
    """

    capability: DisplayCapability
    tool_name: str
    unit_id: str


__all__ = ["CapabilityObservation"]
