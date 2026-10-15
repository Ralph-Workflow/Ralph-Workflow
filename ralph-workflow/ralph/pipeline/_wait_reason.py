"""Closed vocabulary of pipeline wait reasons.

Extracted from :mod:`ralph.pipeline.state` so the wait-state ownership
model has a dedicated module: ``PipelineState.set_waiting`` is the sole
writer of ``is_waiting_state``, and this enum is the sole vocabulary it
accepts.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["WaitReason"]


class WaitReason(StrEnum):
    """Closed set of reasons the pipeline entered the wait state.

    Owned by ``PipelineState.set_waiting``; the wait flag and its reason
    are always set/cleared together through that seam.
    """

    ALL_AGENTS_UNAVAILABLE = "all_agents_unavailable"
    EXHAUSTED_RETRY = "exhausted_retry"
    COOLDOWN = "cooldown"
