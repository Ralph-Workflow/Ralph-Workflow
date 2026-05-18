"""Tests for ralph/mcp/artifacts/bridge.py — MCP bridge layer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from ralph.mcp.artifacts.bridge import (
    BridgeArtifactDeps,
    BridgeConfig,
)
from ralph.mcp.artifacts.file_backend import FileBackend

METHOD_NOT_FOUND_CODE = -32601
INVALID_REQUEST_CODE = -32600


def _object_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _object_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


class MemoryBackend(FileBackend):
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
        self._directories.add(path.parent)
        self._directories.update(path.parent.parents)
        self._files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self._directories.add(destination.parent)
        self._directories.update(destination.parent.parents)
        self._files[destination] = self._files.pop(source)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self._files.pop(path, None)
            return
        del self._files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        if pattern != "*.json":
            return []
        prefix = f"{path}/"
        return sorted(
            candidate
            for candidate in self._files
            if str(candidate).startswith(prefix) and candidate.suffix == ".json"
        )


class TestBridgeConfig:

    def test_default_values(self) -> None:
        config = BridgeConfig()
        assert config.artifact_dir == Path(".agent/artifacts")
        assert config.workspace_root == Path()
        assert config.transport is None

    def test_custom_values(self) -> None:
        transport = MagicMock()
        config = BridgeConfig(
            artifact_dir=Path("/tmp/artifacts"),
            workspace_root=Path("/workspace"),
            transport=transport,
        )
        assert config.artifact_dir == Path("/tmp/artifacts")
        assert config.workspace_root == Path("/workspace")
        assert config.transport is transport

    def test_artifact_dependencies_can_be_injected(self) -> None:
        backend = MemoryBackend()
        deps = BridgeArtifactDeps(backend=backend, now_iso=lambda: "2026-04-15T12:00:00+00:00")
        config = BridgeConfig(artifact_dir=Path("/virtual-artifacts"), artifact_deps=deps)

        assert config.artifact_deps is deps

