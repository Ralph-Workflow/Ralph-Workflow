"""Tests for ralph/mcp/artifacts.py — MCP artifact management."""

from __future__ import annotations

from ralph.mcp.artifacts.store import (
    Artifact,
)


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
