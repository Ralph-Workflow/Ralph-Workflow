"""ArtifactExistsError — raised when attempting to create an artifact that already exists."""

from __future__ import annotations

from ralph.mcp.artifacts._artifact_error import ArtifactError


class ArtifactExistsError(ArtifactError):
    """Raised when attempting to create an artifact that already exists."""


__all__ = ["ArtifactExistsError"]
