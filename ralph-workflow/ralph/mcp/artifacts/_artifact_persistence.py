"""ArtifactPersistence — backend and clock dependencies for artifact operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend

if TYPE_CHECKING:
    from collections.abc import Callable


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


@dataclass(frozen=True)
class ArtifactPersistence:
    """Backend and clock dependencies for artifact persistence operations."""

    backend: FileBackend = DEFAULT_FILE_BACKEND
    now_iso: Callable[[], str] = _utc_now_iso


DEFAULT_ARTIFACT_PERSISTENCE = ArtifactPersistence()

__all__ = ["DEFAULT_ARTIFACT_PERSISTENCE", "ArtifactPersistence", "_utc_now_iso"]
