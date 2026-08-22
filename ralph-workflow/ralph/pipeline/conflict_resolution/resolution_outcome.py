"""Typed terminal result for one conflict-resolution invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
        ResolutionTerminationReason,
    )


@dataclass(frozen=True)
class ResolutionOutcome:
    """Observable resolution attempt outcome used for status and terminal handling."""

    succeeded: bool
    reason: ResolutionTerminationReason | None
    duration_seconds: float
    last_activity_kind: str | None
    last_activity_at: float | None
    unresolved_paths: tuple[str, ...]
