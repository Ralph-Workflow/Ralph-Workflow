"""Internal render data for phase change transitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.display.phase_lifecycle import PhaseExitModel


@dataclass(frozen=True)
class _PhaseChangeRenderData:
    """Canonical display payload for a single phase change."""

    previous_phase: str
    current_phase: str
    exit_model: PhaseExitModel
    transition_context: dict[str, object] | None
