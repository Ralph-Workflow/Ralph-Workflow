"""Tests for AGY workspace MCP config injection."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.transport.agy import agy_workspace_mcp_endpoint

if TYPE_CHECKING:
    from pathlib import Path


def _read_config(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_ralph_only(config: dict[str, object], endpoint: str) -> None:
    servers = config["mcpServers"]
    assert isinstance(servers, dict)
    assert list(servers.keys()) == ["ralph"]
    ralph = servers["ralph"]
    assert isinstance(ralph, dict)
    assert ralph["serverUrl"] == endpoint


def test_agy_workspace_mcp_endpoint_creates_config_when_absent(tmp_path: Path) -> None:
    config_path = tmp_path / ".agents" / "mcp_config.json"
    endpoint = "http://127.0.0.1:9999/mcp"

    assert not config_path.exists()

    with agy_workspace_mcp_endpoint(tmp_path, endpoint):
        assert config_path.exists()
        _assert_ralph_only(_read_config(config_path), endpoint)

    assert not config_path.exists()


def test_agy_workspace_mcp_endpoint_writes_ralph_only_when_existing_servers_present(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / ".agents" / "mcp_config.json"
    original_text = json.dumps(
        {
            "mcpServers": {
                "other-server": {"serverUrl": "http://other.example/mcp"},
            }
        },
        indent=2,
    )
    config_path.parent.mkdir(parents=True)
    config_path.write_text(original_text, encoding="utf-8")
    endpoint = "http://127.0.0.1:9999/mcp"

    with agy_workspace_mcp_endpoint(tmp_path, endpoint):
        _assert_ralph_only(_read_config(config_path), endpoint)

    assert config_path.read_text(encoding="utf-8") == original_text


def test_agy_workspace_mcp_endpoint_overwrites_stale_ralph_entry(tmp_path: Path) -> None:
    config_path = tmp_path / ".agents" / "mcp_config.json"
    original_text = json.dumps(
        {
            "mcpServers": {
                "ralph": {"serverUrl": "http://old.example/mcp"},
                "other-server": {"serverUrl": "http://other.example/mcp"},
            }
        },
        indent=2,
    )
    config_path.parent.mkdir(parents=True)
    config_path.write_text(original_text, encoding="utf-8")
    endpoint = "http://127.0.0.1:9999/mcp"

    with agy_workspace_mcp_endpoint(tmp_path, endpoint):
        _assert_ralph_only(_read_config(config_path), endpoint)

    assert config_path.read_text(encoding="utf-8") == original_text


def test_agy_workspace_mcp_endpoint_restores_on_exception(tmp_path: Path) -> None:
    config_path = tmp_path / ".agents" / "mcp_config.json"
    original_text = json.dumps(
        {"mcpServers": {"other-server": {"serverUrl": "http://other.example/mcp"}}},
        indent=2,
    )
    config_path.parent.mkdir(parents=True)
    config_path.write_text(original_text, encoding="utf-8")
    endpoint = "http://127.0.0.1:9999/mcp"

    with pytest.raises(RuntimeError, match="boom"), agy_workspace_mcp_endpoint(
        tmp_path, endpoint
    ):
        raise RuntimeError("boom")

    assert config_path.read_text(encoding="utf-8") == original_text


def test_agy_workspace_mcp_endpoint_creates_agents_dir_if_missing(tmp_path: Path) -> None:
    config_path = tmp_path / ".agents" / "mcp_config.json"
    endpoint = "http://127.0.0.1:9999/mcp"

    assert not config_path.parent.exists()

    with agy_workspace_mcp_endpoint(tmp_path, endpoint):
        assert config_path.exists()
        _assert_ralph_only(_read_config(config_path), endpoint)
        assert config_path.parent.exists()

    assert not config_path.exists()
