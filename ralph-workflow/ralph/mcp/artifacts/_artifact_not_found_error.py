"""ArtifactNotFoundError — raised when an artifact is not found."""

from __future__ import annotations

from ralph.mcp.artifacts._artifact_error import ArtifactError


class ArtifactNotFoundError(ArtifactError):
    """Raised when an artifact is not found."""


__all__ = ["ArtifactNotFoundError"]
