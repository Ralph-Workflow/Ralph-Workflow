"""Tests for ralph/mcp/artifacts.py — MCP artifact management."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ralph.mcp.artifacts import (
    Artifact,
    ArtifactExistsError,
    ArtifactNotFoundError,
    ArtifactPersistence,
    ArtifactSubmitOptions,
    ArtifactUpdateOptions,
    delete_artifact,
    get_artifact,
    list_artifacts,
    submit_artifact,
    update_artifact,
)


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


MULTI_ARTIFACT_COUNT = 2


class TestArtifact:
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
        backend = FakeFileBackend()
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


class TestGetArtifact:
    def test_get_artifact_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            submit_artifact(
                artifact_dir,
                name="retrieve_me",
                artifact_type="review",
                content={"rating": 5},
            )

            artifact = get_artifact(artifact_dir, "retrieve_me")

            assert artifact.name == "retrieve_me"
            assert artifact.artifact_type == "review"
            assert artifact.content == {"rating": 5}

    def test_get_artifact_not_found_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            with pytest.raises(ArtifactNotFoundError) as exc_info:
                get_artifact(artifact_dir, "nonexistent")
            assert "not found" in str(exc_info.value)


class TestListArtifacts:
    def test_list_artifacts_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            artifacts = list_artifacts(artifact_dir)
            assert artifacts == []

    def test_list_artifacts_multiple(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            submit_artifact(artifact_dir, name="artifact_a", artifact_type="type_a", content={})
            submit_artifact(artifact_dir, name="artifact_b", artifact_type="type_b", content={})

            artifacts = list_artifacts(artifact_dir)

            assert len(artifacts) == MULTI_ARTIFACT_COUNT
            names = {a.name for a in artifacts}
            assert names == {"artifact_a", "artifact_b"}

    def test_list_artifacts_sorted_by_updated_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            submit_artifact(artifact_dir, name="first", artifact_type="code", content={})
            submit_artifact(artifact_dir, name="second", artifact_type="code", content={})

            artifacts = list_artifacts(artifact_dir)

            assert len(artifacts) == MULTI_ARTIFACT_COUNT
            assert artifacts[0].name == "first"
            assert artifacts[1].name == "second"


class TestUpdateArtifact:
    def test_update_artifact_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            submit_artifact(
                artifact_dir,
                name="updatable",
                artifact_type="code",
                content={"original": True},
            )

            artifact = update_artifact(
                artifact_dir,
                name="updatable",
                options=ArtifactUpdateOptions(content={"original": True, "updated": True}),
            )

            assert artifact.content == {"original": True, "updated": True}

    def test_update_artifact_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            submit_artifact(
                artifact_dir,
                name="meta_update",
                artifact_type="code",
                content={},
                options=ArtifactSubmitOptions(metadata={"v": 1}),
            )

            artifact = update_artifact(
                artifact_dir,
                name="meta_update",
                options=ArtifactUpdateOptions(metadata={"v": 2, "new": True}),
            )

            assert artifact.metadata == {"v": 2, "new": True}

    def test_update_artifact_not_found_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            with pytest.raises(ArtifactNotFoundError):
                update_artifact(
                    artifact_dir,
                    name="nonexistent",
                    options=ArtifactUpdateOptions(content={"x": 1}),
                )


class TestDeleteArtifact:
    def test_delete_artifact_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            submit_artifact(artifact_dir, name="to_delete", artifact_type="code", content={})

            delete_artifact(artifact_dir, "to_delete")

            artifacts = list_artifacts(artifact_dir)
            assert artifacts == []

    def test_delete_artifact_not_found_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            with pytest.raises(ArtifactNotFoundError):
                delete_artifact(artifact_dir, "nonexistent")
