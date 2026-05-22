"""Tests for ralph/mcp/transport/agy.py."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from ralph.mcp.transport.agy import (
    agy_mcp_config,
    load_existing_agy_upstream_servers,
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
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "serverUrl": "https://api.githubcopilot.com/mcp/",
                    },
                    "filesystem": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                    },
                }
            }
        ),
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
