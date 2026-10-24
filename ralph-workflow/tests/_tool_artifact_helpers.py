"""Shared helpers for the MCP tool-artifact test suites.

The ``_tool_artifact_1_helper_*`` and ``_tool_artifact_2_helper_*`` module
stacks were near-identical duplicates of the same four helpers (an
in-memory artifact backend, a failing-write backend, a workspace double,
and a session double). They are now re-export-only shims over this single
implementation, so the helper logic exists in exactly one place while every
legacy import path keeps working unchanged.

The helper classes are private and exported through public aliases because
the repo-structure policy allows at most one public top-level class per
module and this module carries four helpers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import FileBackend

if TYPE_CHECKING:
    from pathlib import Path


class _MemoryBackend(FileBackend):
    """In-memory artifact backend used by the tool-artifact suites."""

    def __init__(self) -> None:
        self._files: dict[Path, str] = {}
        self._directories: set[Path] = set()

    def exists(self, path: Path) -> bool:
        return path in self._files or path in self._directories

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del exist_ok
        self._directories.add(path)
        if parents:
            self._directories.update(path.parents)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        return self._files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self._store(path, content)

    def _store(self, path: Path, content: str) -> None:
        """Record ``content`` for ``path`` and register its parent directories."""
        self._directories.add(path.parent)
        self._directories.update(path.parent.parents)
        self._files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self._directories.add(destination.parent)
        self._directories.update(destination.parent.parents)
        self._files[destination] = self._files.pop(source)

    def sync_directory(self, path: Path) -> None:
        del path

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self._files.pop(path, None)
            return
        del self._files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        if pattern != "*.json":
            return []
        prefix = f"{path}/"
        return [
            candidate
            for candidate in self._files
            if str(candidate).startswith(prefix) and candidate.suffix == ".json"
        ]


class _FailingArtifactBackend(_MemoryBackend):
    """Memory backend whose writes to one configured path always fail."""

    def __init__(self, failing_path: Path, *, message: str = "artifact store unavailable") -> None:
        super().__init__()
        self._failing_path = failing_path
        self._message = message

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        if path == self._failing_path:
            raise OSError(self._message)
        self._store(path, content)


class _MockWorkspace:
    """Workspace double exposing only ``absolute_path``."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def absolute_path(self, path: str) -> str:
        return str(self.root / path)


class _MockSession:
    """Session double for the artifact tool handlers."""

    session_id: str = "test-session"
    run_id: str = ""
    broker_secret = None
    explore_index: object | None = None

    def __init__(self, drain: str = "development") -> None:
        self.drain = drain

    def check_capability(self, capability: str) -> object:
        return capability in {"artifact.submit", "artifact.plan_read", "artifact.plan_write"}


MemoryBackend = _MemoryBackend
FailingArtifactBackend = _FailingArtifactBackend
MockWorkspace = _MockWorkspace
MockSession = _MockSession

__all__ = [
    "FailingArtifactBackend",
    "MemoryBackend",
    "MockSession",
    "MockWorkspace",
]
