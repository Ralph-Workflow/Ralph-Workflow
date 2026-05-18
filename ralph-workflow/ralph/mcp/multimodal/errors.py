"""Shared multimodal failure taxonomy for Ralph's managed MCP runtime path.

All code that needs to emit or classify a multimodal failure must use these
types rather than constructing ad hoc error strings. This keeps failure
messages consistent and machine-inspectable across capability detection,
tool handlers, upstream normalization, and invoke-time checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class MultimodalFailure:
    """A structured description of why a multimodal operation could not complete."""

    class MultimodalFailureKind(StrEnum):
        """Enumerated reasons why a multimodal operation failed."""

        UNSUPPORTED_MODALITY = "unsupported_modality"
        UNSUPPORTED_RUNTIME_SEAM = "unsupported_runtime_seam"
        UNSUPPORTED_MIME_TYPE = "unsupported_mime_type"
        PAYLOAD_TOO_LARGE = "payload_too_large"
        FILE_READ_ERROR = "file_read_error"
        NO_ACTIVE_MANIFEST = "no_active_manifest"
        PROVIDER_REJECTED = "provider_rejected"
        INVALID_REPLAY_HANDLE = "invalid_replay_handle"
        MISSING_REPLAY_SOURCE = "missing_replay_source"


    kind: MultimodalFailureKind
    message: str
    modality: str | None = None
    provider: str | None = None
    model_id: str | None = None

    def user_message(self) -> str:
        """Return a human-readable failure message suitable for tool output."""
        parts = [self.message]
        if self.modality:
            parts.append(f"modality: {self.modality}")
        if self.provider:
            parts.append(f"provider: {self.provider}")
        if self.model_id:
            parts.append(f"model: {self.model_id}")
        return " | ".join(parts)


MultimodalFailureKind = MultimodalFailure.MultimodalFailureKind


__all__ = [
    "MultimodalFailure",
    "MultimodalFailureKind",
]
