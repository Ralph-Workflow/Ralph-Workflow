"""ArtifactUpdateOptions — options for updating an existing artifact."""

from __future__ import annotations

from dataclasses import dataclass, field

from ralph.mcp.artifacts._artifact_persistence import ArtifactPersistence


@dataclass(frozen=True)
class ArtifactUpdateOptions:
    """Options for updating an existing artifact."""

    content: dict[str, object] | None = None
    metadata: dict[str, object] | None = None
    persistence: ArtifactPersistence = field(default_factory=ArtifactPersistence)


__all__ = ["ArtifactUpdateOptions"]
