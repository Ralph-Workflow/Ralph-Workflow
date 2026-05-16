"""Save-checkpoint pipeline effect."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SaveCheckpointEffect:
    """Effect to save a checkpoint."""

    pass
