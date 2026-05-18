"""ArtifactSubmitOptions — options for artifact submission."""

from __future__ import annotations

from dataclasses import dataclass, field

from ralph.mcp.artifacts._artifact_persistence import ArtifactPersistence


@dataclass(frozen=True)
class ArtifactSubmitOptions:
    """Options for artifact submission."""

    metadata: dict[str, object] | None = None
    overwrite: bool = False
    persistence: ArtifactPersistence = field(default_factory=ArtifactPersistence)


__all__ = ["ArtifactSubmitOptions"]
