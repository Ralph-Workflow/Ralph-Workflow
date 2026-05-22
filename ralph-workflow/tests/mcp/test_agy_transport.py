"""Tests for ralph/mcp/transport/agy.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.transport.agy import (
    agy_mcp_config,
    load_existing_agy_upstream_servers,
    prepare_agy_home,
)

if TYPE_CHECKING:
    import pytest


def test_agy_mcp_config_produces_serverurl_key(tmp_path: Path) -> None:
    """AGY uses serverUrl (not url) as the HTTP key."""
    endpoint = "http://localhost:8080/mcp"
    config = agy_mcp_config(endpoint)

    parsed = json.loads(config)
    assert "mcpServers" in parsed
    assert "ralph" in parsed["mcpServers"]
    ralph_entry = parsed["mcpServers"]["ralph"]
    assert "serverUrl" in ralph_entry
    assert ralph_entry["serverUrl"] == endpoint


def test_load_existing_agy_upstream_servers_returns_empty_when_no_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When no config file exists, returns empty tuple."""
    monkeypatch.setenv("HOME", str(tmp_path))
    result = load_existing_agy_upstream_servers(workspace_path=None)
    assert result == ()


def test_load_existing_agy_upstream_servers_parses_http_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """It parses mcpServers entries with serverUrl key."""
    agy_home = tmp_path / ".gemini" / "antigravity-cli"
    agy_home.mkdir(parents=True)
    config_file = agy_home / "mcp_config.json"
    config_file.write_text(
        json.dumps({
            "mcpServers": {
                "github": {
                    "serverUrl": "https://api.githubcopilot.com/mcp/",
                },
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                },
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))

    result = load_existing_agy_upstream_servers(workspace_path=None)

    names = {s.name for s in result}
    assert "github" in names
    assert "filesystem" in names
    http_servers = [s for s in result if s.transport == "http"]
    assert len(http_servers) == 1
    assert http_servers[0].name == "github"
    assert http_servers[0].url == "https://api.githubcopilot.com/mcp/"


def test_prepare_agy_home_creates_temp_dir(tmp_path: Path) -> None:
    """prepare_agy_home creates an isolated temp directory."""
    result_path, upstreams = prepare_agy_home(
        None,
        workspace_path=tmp_path,
        existing_home=None,
    )

    assert Path(result_path).is_absolute()
    assert Path(result_path).exists()
    assert upstreams == ()


def test_prepare_agy_home_with_endpoint_writes_mcp_config(tmp_path: Path) -> None:
    """When endpoint is provided, writes mcp_config.json with Ralph entry."""
    result_path, _upstreams = prepare_agy_home(
        "http://localhost:8080/mcp",
        workspace_path=tmp_path,
        existing_home=None,
    )

    config_path = Path(result_path) / "antigravity-cli" / "mcp_config.json"
    assert config_path.exists()

    parsed = json.loads(config_path.read_text(encoding="utf-8"))
    assert "mcpServers" in parsed
    assert "ralph" in parsed["mcpServers"]
    assert parsed["mcpServers"]["ralph"]["serverUrl"] == "http://localhost:8080/mcp"


def test_prepare_agy_home_with_no_endpoint_does_not_write_config(tmp_path: Path) -> None:
    """When no endpoint is provided, no mcp_config.json is written."""
    result_path, _upstreams = prepare_agy_home(
        None,
        workspace_path=tmp_path,
        existing_home=None,
    )

    config_path = Path(result_path) / "antigravity-cli" / "mcp_config.json"
    # Should not exist unless there was an existing config mirrored
    assert not config_path.exists()


def test_prepare_agy_home_returns_absolute_path(tmp_path: Path) -> None:
    """Returned path is absolute and usable as GEMINI_HOME env var."""
    result_path, _upstreams = prepare_agy_home(
        None,
        workspace_path=tmp_path,
        existing_home=None,
    )

    # Must be absolute path string
    assert Path(result_path).is_absolute()
    # Must be usable as an env var value (just a string path)
    assert isinstance(result_path, str)


def test_prepare_agy_home_uses_workspace_tmp_dir(tmp_path: Path) -> None:
    """When workspace_path is provided, uses .agent/tmp under workspace."""
    result_path, _ = prepare_agy_home(
        None,
        workspace_path=tmp_path,
        existing_home=None,
    )

    assert result_path.startswith(str(tmp_path))
    assert ".agent" in result_path
    assert "tmp" in result_path
