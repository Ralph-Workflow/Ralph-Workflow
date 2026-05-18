"""Tests for ralph/mcp/artifacts.py — MCP artifact management."""

from __future__ import annotations

import fnmatch
import json
import tempfile
from pathlib import Path

import pytest

from ralph.mcp.artifacts.store import (
    ArtifactExistsError,
    ArtifactPersistence,
    ArtifactSubmitOptions,
    list_artifacts,
    submit_artifact,
)


class _FakeFileBackend:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def exists(self, path: Path) -> bool:
        return str(path) in self._data

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        pass

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        return self._data[str(path)]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        self._data[str(path)] = content

    def replace(self, source: Path, destination: Path) -> None:
        self._data[str(destination)] = self._data.pop(str(source))

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        key = str(path)
        if key in self._data:
            del self._data[key]
        elif not missing_ok:
            raise FileNotFoundError(path)

    def glob(self, path: Path, pattern: str) -> list[Path]:
        return [
            Path(k)
            for k in self._data
            if Path(k).parent == path and fnmatch.fnmatch(Path(k).name, pattern)
        ]


class TestSubmitArtifact:
    def test_submit_artifact_creates_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / ".agent" / "artifacts"

            artifact = submit_artifact(
                artifact_dir,
                name="my_artifact",
                artifact_type="planning",
                content={"plan": "first step"},
            )

            assert artifact.name == "my_artifact"
            assert artifact.artifact_type == "planning"
            assert artifact.content == {"plan": "first step"}

            artifact_path = artifact_dir / "my_artifact.json"
            assert artifact_path.exists()

            stored = json.loads(artifact_path.read_text())
            assert stored["name"] == "my_artifact"
            assert stored["type"] == "planning"

    def test_submit_artifact_with_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            artifact = submit_artifact(
                artifact_dir,
                name="meta_artifact",
                artifact_type="code",
                content={"code": "print('hello')"},
                options=ArtifactSubmitOptions(metadata={"language": "python"}),
            )

            assert artifact.metadata == {"language": "python"}

    def test_submit_artifact_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            submit_artifact(
                artifact_dir,
                name="overwrite_me",
                artifact_type="code",
                content={"version": 1},
            )

            artifact = submit_artifact(
                artifact_dir,
                name="overwrite_me",
                artifact_type="code",
                content={"version": 2},
                options=ArtifactSubmitOptions(overwrite=True),
            )

            assert artifact.content == {"version": 2}

            artifacts = list_artifacts(artifact_dir)
            assert len(artifacts) == 1

    def test_submit_artifact_exists_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            submit_artifact(
                artifact_dir,
                name="duplicate",
                artifact_type="code",
                content={"v": 1},
            )

            with pytest.raises(ArtifactExistsError) as exc_info:
                submit_artifact(
                    artifact_dir,
                    name="duplicate",
                    artifact_type="code",
                    content={"v": 2},
                )
            assert "already exists" in str(exc_info.value)

    def test_submit_artifact_uses_injected_backend_and_clock(self) -> None:
        backend = _FakeFileBackend()
        artifact_dir = Path("/virtual/artifacts")

        artifact = submit_artifact(
            artifact_dir,
            name="virtual",
            artifact_type="planning",
            content={"ok": True},
            options=ArtifactSubmitOptions(
                persistence=ArtifactPersistence(backend=backend, now_iso=lambda: "STATIC-TIME")
            ),
        )

        stored = json.loads(backend.read_text(artifact_dir / "virtual.json"))
        assert artifact.created_at == "STATIC-TIME"
        assert stored["created_at"] == "STATIC-TIME"
