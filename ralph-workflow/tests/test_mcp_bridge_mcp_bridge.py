"""Tests for ralph/mcp/artifacts/bridge.py — MCP bridge layer."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

import pytest

from ralph.mcp.artifacts.bridge import (
    BridgeArtifactDeps,
    BridgeConfig,
    BridgeError,
    MCPBridge,
)
from ralph.mcp.artifacts.store import ArtifactNotFoundError
from tests.test_mcp_bridge_memory_backend import MemoryBackend

if TYPE_CHECKING:
    from pathlib import Path

METHOD_NOT_FOUND_CODE = -32601
INVALID_REQUEST_CODE = -32600


def _object_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _object_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


class TestMCPBridge:
    def _make_bridge(self, transport: MagicMock | None = None) -> MCPBridge:
        config = BridgeConfig(transport=transport)
        return MCPBridge(config)

    def test_registration(self) -> None:
        bridge = self._make_bridge()
        handler = MagicMock(return_value={"result": "ok"})
        bridge.register_tool(
            name="test_tool",
            description="A test tool",
            input_schema={"type": "object"},
            handler=handler,
        )
        assert "test_tool" in bridge._tools
        assert bridge._tools["test_tool"].name == "test_tool"

    def test_tool_called_success(self) -> None:
        bridge = self._make_bridge()
        handler = MagicMock(return_value={"result": "value"})
        bridge.register_tool(
            name="test_tool",
            description="A test tool",
            input_schema={},
            handler=handler,
        )
        result = bridge.tool_called("test_tool", {"arg": "value"})
        assert result["success"] is True
        assert result["result"] == {"result": "value"}
        handler.assert_called_once_with(arg="value")

    def test_tool_called_not_found(self) -> None:
        bridge = self._make_bridge()
        with pytest.raises(BridgeError, match="Tool 'nonexistent' not found"):
            bridge.tool_called("nonexistent", {})

    def test_tool_called_exception(self) -> None:
        bridge = self._make_bridge()
        handler = MagicMock(side_effect=RuntimeError("test error"))
        bridge.register_tool(
            name="failing_tool",
            description="A failing tool",
            input_schema={},
            handler=handler,
        )
        result = bridge.tool_called("failing_tool", {})
        assert result["success"] is False
        error = cast("str", result["error"])
        assert "test error" in error

    def test_submit_artifact_success(self, tmp_path: Path) -> None:
        bridge = MCPBridge(
            BridgeConfig(
                workspace_root=tmp_path,
                artifact_dir=tmp_path / ".agent" / "artifacts",
                run_id="bridge-run",
            )
        )

        result = bridge.submit_artifact_mcp(
            name="test_artifact",
            artifact_type="code",
            content={"code": "print('hello')"},
        )
        assert result["success"] is True
        artifact = _object_dict(result["artifact"])
        assert artifact["name"] == "test_artifact"
        assert artifact["type"] == "code"
        assert _object_dict(artifact["content"])["code"] == "print('hello')"

    def test_submit_artifact_exists(self, tmp_path: Path) -> None:
        bridge = MCPBridge(
            BridgeConfig(
                workspace_root=tmp_path,
                artifact_dir=tmp_path / ".agent" / "artifacts",
                run_id="bridge-run",
            )
        )
        bridge.submit_artifact_mcp(
            name="test_artifact",
            artifact_type="code",
            content={"code": "print('hello')"},
        )

        result = bridge.submit_artifact_mcp(
            name="test_artifact",
            artifact_type="code",
            content={"code": "print('hello again')"},
        )
        assert result["success"] is False
        error = cast("str", result["error"])
        assert "already exists" in error

    def test_submit_get_list_artifacts_use_configured_artifact_dir(
        self,
        tmp_path: Path,
    ) -> None:
        custom_dir = tmp_path / "custom-artifacts"
        bridge = MCPBridge(
            BridgeConfig(
                workspace_root=tmp_path,
                artifact_dir=custom_dir,
                run_id="bridge-run",
            )
        )

        submit_result = bridge.submit_artifact_mcp(
            name="test_artifact",
            artifact_type="code",
            content={"code": "print('hello')"},
        )
        assert submit_result["success"] is True

        get_result = bridge.get_artifact_mcp("test_artifact")
        assert get_result["success"] is True
        artifact = _object_dict(get_result["artifact"])
        assert artifact["name"] == "test_artifact"

        list_result = bridge.list_artifacts_mcp()
        assert list_result["success"] is True
        artifacts = _object_list(list_result["artifacts"])
        assert len(artifacts) == 1
        first_artifact = _object_dict(artifacts[0])
        assert first_artifact["name"] == "test_artifact"

        assert not (tmp_path / ".agent" / "artifacts" / "test_artifact.json").exists()
        assert (custom_dir / "test_artifact.json").exists()

    @patch("ralph.mcp.artifacts.bridge.get_artifact")
    def test_get_artifact_success(self, mock_get: MagicMock) -> None:
        bridge = self._make_bridge()
        artifact_mock = MagicMock()
        artifact_mock.to_dict.return_value = {"name": "test_artifact"}
        mock_get.return_value = artifact_mock

        result = bridge.get_artifact_mcp("test_artifact")
        assert result["success"] is True
        artifact = _object_dict(result["artifact"])
        assert artifact["name"] == "test_artifact"

    @patch("ralph.mcp.artifacts.bridge.get_artifact")
    def test_get_artifact_not_found(self, mock_get: MagicMock) -> None:
        bridge = self._make_bridge()
        mock_get.side_effect = ArtifactNotFoundError("Artifact not found")

        result = bridge.get_artifact_mcp("nonexistent")
        assert result["success"] is False
        error = cast("str", result["error"])
        assert "not found" in error

    @patch("ralph.mcp.artifacts.bridge.list_artifacts")
    def test_list_artifacts_success(self, mock_list: MagicMock) -> None:
        bridge = self._make_bridge()
        artifact_mock = MagicMock()
        artifact_mock.to_dict.return_value = {"name": "artifact1"}
        mock_list.return_value = [artifact_mock]

        result = bridge.list_artifacts_mcp()
        assert result["success"] is True
        artifacts = _object_list(result["artifacts"])
        assert len(artifacts) == 1
        first_artifact = _object_dict(artifacts[0])
        assert first_artifact["name"] == "artifact1"

    def test_bridge_artifact_entrypoints_support_injected_backend_without_patching_globals(
        self,
        tmp_path: Path,
    ) -> None:
        backend = MemoryBackend()
        artifact_dir = tmp_path / ".agent" / "artifacts"
        bridge = MCPBridge(
            BridgeConfig(
                workspace_root=tmp_path,
                artifact_dir=artifact_dir,
                artifact_deps=BridgeArtifactDeps(
                    backend=backend,
                    now_iso=lambda: "2026-04-15T12:00:00+00:00",
                ),
                run_id="bridge-run",
            )
        )

        submit_result = bridge.submit_artifact_mcp(
            name="test_artifact",
            artifact_type="code",
            content={"code": "print('hello')"},
            metadata={"source": "test"},
        )
        get_result = bridge.get_artifact_mcp("test_artifact")
        list_result = bridge.list_artifacts_mcp()

        stored = json.loads(backend.read_text(artifact_dir / "test_artifact.json"))
        assert submit_result["success"] is True
        assert stored["metadata"] == {"source": "test"}
        assert get_result["success"] is True
        assert _object_dict(get_result["artifact"])["name"] == "test_artifact"
        listed = _object_list(list_result["artifacts"])
        assert [_object_dict(item)["name"] for item in listed] == ["test_artifact"]
