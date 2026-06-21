"""Tests for ralph/mcp/transport/nanocoder.py unsafe_mode handling."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.mcp.transport.nanocoder import build_nanocoder_mcp_config

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_unsafe_mode_false_preserves_existing_env_var_servers() -> None:
    """unsafe_mode=False keeps existing env-var servers and adds Ralph (default behavior)."""
    existing = json.dumps(
        {
            "mcpServers": {
                "other": {
                    "transport": "http",
                    "url": "http://o.example/mcp",
                }
            }
        }
    )

    config_text, _upstreams = build_nanocoder_mcp_config(existing, "http://r.example/mcp")

    parsed = json.loads(config_text)
    servers = parsed["mcpServers"]
    assert "other" in servers
    assert "ralph" in servers
    ralph = servers["ralph"]
    assert ralph["transport"] == "http"
    assert ralph["url"] == "http://r.example/mcp"


def test_unsafe_mode_true_merges_user_config_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """unsafe_mode=True loads user nanocoder config files and adds Ralph on top."""
    config_dir = tmp_path / "nanocoder-config"
    config_dir.mkdir()
    (config_dir / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "user-server": {
                        "transport": "http",
                        "url": "http://user.example/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NANOCODER_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("HOME", raising=False)

    config_text, _upstreams = build_nanocoder_mcp_config(
        None, "http://r.example/mcp", unsafe_mode=True
    )

    parsed = json.loads(config_text)
    servers = parsed["mcpServers"]
    assert "user-server" in servers
    assert "ralph" in servers
    assert servers["ralph"]["url"] == "http://r.example/mcp"


def test_unsafe_mode_true_overwrites_stale_ralph_in_user_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """unsafe_mode=True still lets Ralph win on name collision against user config files."""
    config_dir = tmp_path / "nanocoder-config"
    config_dir.mkdir()
    (config_dir / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "ralph": {
                        "transport": "http",
                        "url": "http://old.example/mcp",
                    },
                    "user-server": {
                        "transport": "http",
                        "url": "http://user.example/mcp",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NANOCODER_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("HOME", raising=False)

    config_text, _upstreams = build_nanocoder_mcp_config(
        None, "http://r.example/mcp", unsafe_mode=True
    )

    parsed = json.loads(config_text)
    servers = parsed["mcpServers"]
    assert servers["ralph"]["url"] == "http://r.example/mcp"
    assert "user-server" in servers


def test_unsafe_mode_true_falls_back_to_ralph_only_when_user_config_malformed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """unsafe_mode=True survives a malformed user config file by ignoring it."""
    config_dir = tmp_path / "nanocoder-config"
    config_dir.mkdir()
    (config_dir / ".mcp.json").write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("NANOCODER_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("HOME", raising=False)

    config_text, _upstreams = build_nanocoder_mcp_config(
        None, "http://r.example/mcp", unsafe_mode=True
    )

    parsed = json.loads(config_text)
    servers = parsed["mcpServers"]
    assert list(servers.keys()) == ["ralph"]
    assert servers["ralph"]["url"] == "http://r.example/mcp"


def test_malformed_env_var_payload_falls_back_to_ralph_only() -> None:
    """Existing env-var-merge default handles malformed payloads by ignoring them."""
    config_text, _upstreams = build_nanocoder_mcp_config("{not valid json", "http://r.example/mcp")

    parsed = json.loads(config_text)
    servers = parsed["mcpServers"]
    assert list(servers.keys()) == ["ralph"]


def test_unsafe_mode_true_merges_existing_env_and_user_config_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """unsafe_mode=True merges both env-sourced existing payload and file config."""
    env_existing = json.dumps(
        {
            "mcpServers": {
                "env-http": {"transport": "http", "url": "http://env.example/mcp"},
            }
        }
    )
    config_dir = tmp_path / "nanocoder-config"
    config_dir.mkdir()
    (config_dir / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "file-http": {"transport": "http", "url": "http://file.example/mcp"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NANOCODER_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("HOME", raising=False)

    config_text, _upstreams = build_nanocoder_mcp_config(
        env_existing, "http://ralph.example/mcp", unsafe_mode=True
    )

    parsed = json.loads(config_text)
    servers = parsed["mcpServers"]
    assert "env-http" in servers
    assert "file-http" in servers
    assert "ralph" in servers
    assert servers["ralph"]["url"] == "http://ralph.example/mcp"
    assert servers["env-http"]["url"] == "http://env.example/mcp"
    assert servers["file-http"]["url"] == "http://file.example/mcp"
