"""MCP artifact handling.

Provides artifact submission, retrieval, and management for MCP interactions.
Artifacts are JSON files stored in the workspace's .agent/artifacts/ directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from loguru import logger

from ralph.mcp.artifacts._artifact_exists_error import ArtifactExistsError
from ralph.mcp.artifacts._artifact_not_found_error import ArtifactNotFoundError
from ralph.mcp.artifacts._artifact_persistence import ArtifactPersistence, _utc_now_iso
from ralph.mcp.artifacts._artifact_submit_options import ArtifactSubmitOptions
from ralph.mcp.artifacts._artifact_update_options import ArtifactUpdateOptions
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend


@dataclass
class Artifact:
    """Represents an MCP artifact.

    Attributes:
        name: Unique artifact name.
        artifact_type: Type identifier (e.g., "planning", "code", "review").
        content: Artifact content as a dictionary.
        created_at: ISO timestamp when artifact was created.
        updated_at: ISO timestamp when artifact was last updated.
        metadata: Optional metadata dictionary.
    """

    name: str
    artifact_type: str
    content: dict[str, object]
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Convert artifact to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "type": self.artifact_type,
            "content": self.content,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Artifact:
        """Create an artifact from a dictionary."""
        return cls(
            name=cast("str", data.get("name", "")),
            artifact_type=cast("str", data.get("type", "unknown")),
            content=cast("dict[str, object]", data.get("content", {})),
            created_at=cast("str", data.get("created_at", _utc_now_iso())),
            updated_at=cast("str", data.get("updated_at", _utc_now_iso())),
            metadata=cast("dict[str, object]", data.get("metadata", {})),
        )


def submit_artifact(
    artifact_dir: Path,
    name: str,
    artifact_type: str,
    content: dict[str, object],
    options: ArtifactSubmitOptions | None = None,
) -> Artifact:
    """Submit a new artifact.

    Args:
        artifact_dir: Directory to store artifacts (e.g., .agent/artifacts/).
        name: Unique artifact name.
        artifact_type: Type of artifact.
        content: Artifact content.
        options: Optional submission options.

    Returns:
        The created artifact.

    Raises:
        ArtifactExistsError: If artifact exists and overwrite is False.
    """
    opts = options or ArtifactSubmitOptions()
    backend = opts.persistence.backend
    backend.mkdir(artifact_dir, parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{name}.json"

    if backend.exists(artifact_path) and not opts.overwrite:
        raise ArtifactExistsError(f"Artifact '{name}' already exists")

    timestamp = opts.persistence.now_iso()
    artifact = Artifact(
        name=name,
        artifact_type=artifact_type,
        content=content,
        created_at=timestamp,
        updated_at=timestamp,
        metadata=opts.metadata or {},
    )

    backend.write_text(artifact_path, json.dumps(artifact.to_dict(), indent=2))
    logger.debug("Submitted artifact: {} at {}", name, artifact_path)
    return artifact


def get_artifact(
    artifact_dir: Path, name: str, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> Artifact:
    """Retrieve an artifact by name.

    Args:
        artifact_dir: Directory where artifacts are stored.
        name: Artifact name.

    Returns:
        The artifact.

    Raises:
        ArtifactNotFoundError: If artifact does not exist.
    """
    artifact_path = artifact_dir / f"{name}.json"
    if not backend.exists(artifact_path):
        raise ArtifactNotFoundError(f"Artifact '{name}' not found")

    data = cast("dict[str, object]", json.loads(backend.read_text(artifact_path)))
    return Artifact.from_dict(data)


def list_artifacts(
    artifact_dir: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> list[Artifact]:
    """List all artifacts in the directory.

    Args:
        artifact_dir: Directory where artifacts are stored.

    Returns:
        List of artifacts.
    """
    artifacts_dir = Path(artifact_dir)
    if not backend.exists(artifacts_dir):
        return []

    artifacts: list[Artifact] = []
    for path in backend.glob(artifacts_dir, "*.json"):
        try:
            data = cast("dict[str, object]", json.loads(backend.read_text(path)))
            artifacts.append(Artifact.from_dict(data))
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("Failed to read artifact {}: {}", path, exc)

    return sorted(artifacts, key=_artifact_updated_at)


def _artifact_updated_at(artifact: Artifact) -> str:
    return artifact.updated_at


def update_artifact(
    artifact_dir: Path,
    name: str,
    options: ArtifactUpdateOptions | None = None,
) -> Artifact:
    """Update an existing artifact.

    Args:
        artifact_dir: Directory where artifacts are stored.
        name: Artifact name.
        content: New content (merged with existing).
        metadata: New metadata (merged with existing).

    Returns:
        The updated artifact.

    Raises:
        ArtifactNotFoundError: If artifact does not exist.
    """
    opts = options or ArtifactUpdateOptions()
    backend = opts.persistence.backend
    artifact = get_artifact(artifact_dir, name, backend=backend)

    if opts.content is not None:
        artifact.content.update(opts.content)
    if opts.metadata is not None:
        artifact.metadata.update(opts.metadata)

    artifact.updated_at = opts.persistence.now_iso()

    artifact_path = artifact_dir / f"{name}.json"
    backend.write_text(artifact_path, json.dumps(artifact.to_dict(), indent=2))
    logger.debug("Updated artifact: {}", name)
    return artifact


def delete_artifact(
    artifact_dir: Path, name: str, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> None:
    """Delete an artifact.

    Args:
        artifact_dir: Directory where artifacts are stored.
        name: Artifact name.

    Raises:
        ArtifactNotFoundError: If artifact does not exist.
    """
    artifact_path = artifact_dir / f"{name}.json"
    if not backend.exists(artifact_path):
        raise ArtifactNotFoundError(f"Artifact '{name}' not found")
    backend.unlink(artifact_path)
    logger.debug("Deleted artifact: {}", name)


__all__ = [
    "Artifact",
    "ArtifactExistsError",
    "ArtifactNotFoundError",
    "ArtifactPersistence",
    "ArtifactSubmitOptions",
    "ArtifactUpdateOptions",
    "delete_artifact",
    "get_artifact",
    "list_artifacts",
    "submit_artifact",
    "update_artifact",
]
