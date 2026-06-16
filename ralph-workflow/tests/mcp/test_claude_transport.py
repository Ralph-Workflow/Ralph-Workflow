"""Tests for ralph/mcp/transport/claude.py."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.mcp.transport.claude import (
    claude_mcp_config,
    load_existing_claude_upstream_servers,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_claude_mcp_config_produces_http_url_key(tmp_path: Path) -> None:
    """Claude uses url as the HTTP key."""
    endpoint = "http://localhost:8080/mcp"
    config = claude_mcp_config(endpoint)

    parsed = json.loads(config)
    assert "mcpServers" in parsed
    assert "ralph" in parsed["mcpServers"]
    ralph_entry = parsed["mcpServers"]["ralph"]
    assert ralph_entry["type"] == "http"
    assert ralph_entry["url"] == endpoint


def test_load_existing_claude_upstream_servers_returns_empty_when_no_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When no config file exists, returns empty tuple."""
    monkeypatch.setenv("HOME", str(tmp_path))

    result = load_existing_claude_upstream_servers(workspace_path=None)

    assert result == ()


def test_load_existing_claude_upstream_servers_parses_http_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """It parses ~/.claude.json HTTP entries."""
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / ".claude.json"
    config_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "type": "http",
                        "url": "https://api.githubcopilot.com/mcp/",
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

    result = load_existing_claude_upstream_servers(workspace_path=None)

    names = {s.name for s in result}
    assert "github" in names
    assert "filesystem" in names
    http_servers = [s for s in result if s.transport == "http"]
    assert len(http_servers) == 1
    assert http_servers[0].name == "github"
    assert http_servers[0].url == "https://api.githubcopilot.com/mcp/"


def test_load_existing_claude_upstream_servers_reads_workspace_mcp_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """It reads workspace-level .mcp.json."""
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "project"
    workspace.mkdir()
    config_file = workspace / ".mcp.json"
    config_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "ws-upstream": {
                        "type": "http",
                        "url": "http://workspace-upstream:7777/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = load_existing_claude_upstream_servers(workspace_path=workspace)

    names = {s.name for s in result}
    assert "ws-upstream" in names
    http_servers = [s for s in result if s.name == "ws-upstream"]
    assert len(http_servers) == 1
    assert http_servers[0].url == "http://workspace-upstream:7777/mcp"


def test_load_existing_claude_upstream_servers_workspace_overrides_global_on_name_collision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Workspace config overrides global config when names collide."""
    monkeypatch.setenv("HOME", str(tmp_path))
    global_config = tmp_path / ".claude.json"
    global_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared-server": {
                        "type": "http",
                        "url": "https://global.example.invalid/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    workspace = tmp_path / "project"
    workspace.mkdir()
    workspace_config = workspace / ".mcp.json"
    workspace_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared-server": {
                        "type": "http",
                        "url": "https://workspace.example.invalid/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = load_existing_claude_upstream_servers(workspace_path=workspace)

    assert len(result) == 1
    assert result[0].name == "shared-server"
    assert result[0].url == "https://workspace.example.invalid/mcp"


def test_claude_mcp_config_unsafe_mode_merges_existing_servers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """unsafe_mode=True merges ~/.claude.json and workspace .mcp.json entries with Ralph."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "type": "http",
                        "url": "https://api.example.com/mcp/",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "workspace-upstream": {
                        "type": "http",
                        "url": "http://workspace-upstream:7777/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = json.loads(
        claude_mcp_config("http://127.0.0.1:9999/mcp", workspace_path=workspace, unsafe_mode=True)
    )

    servers = config["mcpServers"]
    assert "github" in servers
    assert "workspace-upstream" in servers
    assert "ralph" in servers
    ralph = servers["ralph"]
    assert ralph["type"] == "http"
    assert ralph["url"] == "http://127.0.0.1:9999/mcp"


def test_claude_mcp_config_unsafe_mode_false_keeps_ralph_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """unsafe_mode=False (default) returns a Ralph-only mcpServers payload."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "type": "http",
                        "url": "https://api.example.com/mcp/",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = json.loads(
        claude_mcp_config("http://127.0.0.1:9999/mcp", workspace_path=tmp_path, unsafe_mode=False)
    )

    assert list(config["mcpServers"].keys()) == ["ralph"]


def test_claude_mcp_config_unsafe_mode_overwrites_stale_ralph(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """unsafe_mode=True replaces a stale ralph entry but keeps other servers."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "ralph": {
                        "type": "http",
                        "url": "http://old.example/mcp",
                    },
                    "github": {
                        "type": "http",
                        "url": "https://api.example.com/mcp/",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    config = json.loads(
        claude_mcp_config("http://127.0.0.1:9999/mcp", workspace_path=tmp_path, unsafe_mode=True)
    )

    servers = config["mcpServers"]
    assert servers["ralph"]["url"] == "http://127.0.0.1:9999/mcp"
    assert servers["github"]["url"] == "https://api.example.com/mcp/"


def test_load_existing_claude_upstream_servers_skips_missing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Missing ~/.claude.json does not block workspace config loading."""
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "project"
    workspace.mkdir()
    config_file = workspace / ".mcp.json"
    config_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "workspace-only": {
                        "type": "http",
                        "url": "https://workspace-only.example.invalid/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = load_existing_claude_upstream_servers(workspace_path=workspace)

    assert len(result) == 1
    assert result[0].name == "workspace-only"
    assert result[0].url == "https://workspace-only.example.invalid/mcp"
