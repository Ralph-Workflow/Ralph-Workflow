"""TypedArtifactValidationError — raised when a typed artifact payload is malformed."""

from __future__ import annotations


class TypedArtifactValidationError(ValueError):
    """Raised when a typed artifact payload is malformed."""


__all__ = ["TypedArtifactValidationError"]
