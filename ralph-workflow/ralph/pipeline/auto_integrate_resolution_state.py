"""Preserve durable unresolved integration evidence at optional integration seams."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.rebase_state import RebaseState


def retains_unresolved_resolution_state(state: RebaseState) -> bool:
    """Return whether a phase-boundary integration attempt must retain ``state``."""
    return state.integration_unresolved or state.resolution_exhausted
