"""Preserve durable unresolved integration evidence at optional integration seams."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.integration_resolution import persisted_integration_resolution_verdict

if TYPE_CHECKING:
    from ralph.pipeline.rebase_state import RebaseState


def retains_unresolved_resolution_state(state: RebaseState) -> bool:
    """Return whether durable integration evidence owns the phase boundary."""
    return persisted_integration_resolution_verdict(state) is not None
