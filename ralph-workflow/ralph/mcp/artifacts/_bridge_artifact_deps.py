"""BridgeArtifactDeps — injectable artifact dependencies for the MCP bridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.mcp.artifacts._artifact_persistence import (
    DEFAULT_ARTIFACT_PERSISTENCE,
    ArtifactPersistence,
)
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class BridgeArtifactDeps:
    """Dependencies injected into bridge artifact operations."""

    backend: FileBackend = DEFAULT_FILE_BACKEND
    now_iso: Callable[[], str] = DEFAULT_ARTIFACT_PERSISTENCE.now_iso

    @property
    def persistence(self) -> ArtifactPersistence:
        return ArtifactPersistence(backend=self.backend, now_iso=self.now_iso)


DEFAULT_BRIDGE_ARTIFACT_DEPS = BridgeArtifactDeps()

__all__ = ["DEFAULT_BRIDGE_ARTIFACT_DEPS", "BridgeArtifactDeps"]
