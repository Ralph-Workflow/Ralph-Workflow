"""Identity dataclass for multimodal model capability detection."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MultimodalModelIdentity:
    """Identifies the provider and model for capability detection."""

    provider: str
    model_id: str | None = None
    transport: str | None = None

    def is_known(self) -> bool:
        """Return True if the provider identity is resolved (not 'unknown')."""
        return self.provider != "unknown"
