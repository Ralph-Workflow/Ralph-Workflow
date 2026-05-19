"""Enumerated reasons why a multimodal operation failed."""

from __future__ import annotations

from enum import StrEnum


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
