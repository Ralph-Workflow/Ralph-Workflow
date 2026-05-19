"""Tests for ralph/mcp/artifacts/bridge.py — MCP bridge layer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from ralph.mcp.artifacts.bridge import (
    BridgeArtifactDeps,
    BridgeConfig,
)

from .test_mcp_bridge_bridge_config_helpers import MemoryBackend

METHOD_NOT_FOUND_CODE = -32601
INVALID_REQUEST_CODE = -32600


def _object_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _object_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


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
