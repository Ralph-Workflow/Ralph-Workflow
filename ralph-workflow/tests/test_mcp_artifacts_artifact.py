"""Tests for ralph/mcp/artifacts.py — MCP artifact management."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.store import (
    Artifact,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestArtifact:

    class FakeFileBackend:
        def __init__(self) -> None:
            self.files: dict[Path, str] = {}
            self.directories: set[Path] = set()

        def exists(self, path: Path) -> bool:
            return path in self.files or path in self.directories

        def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
            self.directories.add(path)

        def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
            return self.files[path]

        def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
            self.files[path] = content

        def replace(self, source: Path, destination: Path) -> None:
            self.files[destination] = self.files.pop(source)

        def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
            if missing_ok:
                self.files.pop(path, None)
                return
            del self.files[path]

        def glob(self, path: Path, pattern: str) -> list[Path]:
            suffix = pattern.replace("*", "")
            return [
                candidate
                for candidate in self.files
                if candidate.parent == path and candidate.name.endswith(suffix)
            ]

    def test_to_dict(self) -> None:
        artifact = Artifact(
            name="test_artifact",
            artifact_type="code",
            content={"foo": "bar"},
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
            metadata={"key": "value"},
        )
        result = artifact.to_dict()
        assert result["name"] == "test_artifact"
        assert result["type"] == "code"
        assert result["content"] == {"foo": "bar"}
        assert result["created_at"] == "2024-01-01T00:00:00"
        assert result["updated_at"] == "2024-01-01T00:00:00"
        assert result["metadata"] == {"key": "value"}

    def test_from_dict(self) -> None:
        data: dict[str, object] = {
            "name": "test_artifact",
            "type": "review",
            "content": {"baz": "qux"},
            "created_at": "2024-01-02T00:00:00",
            "updated_at": "2024-01-02T00:00:00",
            "metadata": {"tag": "test"},
        }
        artifact = Artifact.from_dict(data)
        assert artifact.name == "test_artifact"
        assert artifact.artifact_type == "review"
        assert artifact.content == {"baz": "qux"}
        assert artifact.created_at == "2024-01-02T00:00:00"
        assert artifact.updated_at == "2024-01-02T00:00:00"
        assert artifact.metadata == {"tag": "test"}

    def test_from_dict_with_defaults(self) -> None:
        data: dict[str, object] = {"name": "minimal"}
        artifact = Artifact.from_dict(data)
        assert artifact.name == "minimal"
        assert artifact.artifact_type == "unknown"
        assert artifact.content == {}
        assert artifact.metadata == {}

    def test_from_dict_missing_content(self) -> None:
        data: dict[str, object] = {"name": "test", "type": "code"}
        artifact = Artifact.from_dict(data)
        assert artifact.content == {}


FakeFileBackend = TestArtifact.FakeFileBackend
