"""Tests for ralph/mcp/transport/common.py:merge_existing_upstreams helper.

These tests verify that the unsafe_mode merge logic is consolidated into a single
helper that routes to the correct per-agent loader based on agent_name.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from ralph.mcp.transport.common import merge_existing_upstreams


class TestMergeExistingUpstreams:
    """Tests for merge_existing_upstreams(agent_name, current_config, *, unsafe_mode)."""

    def test_unsafe_mode_false_keeps_only_ralph_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """unsafe_mode=False drops non-ralph entries and keeps only the ralph entry."""
        monkeypatch.setenv("HOME", str(tmp_path))

        (tmp_path / ".claude.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "github": {
                            "type": "http",
                            "url": "https://api.example.com/mcp/",
                        },
                        "ralph": {
                            "type": "http",
                            "url": "http://old.example/mcp",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )

        current_config = {
            "mcpServers": {"ralph": {"type": "http", "url": "http://127.0.0.1:9999/mcp"}}
        }

        result = merge_existing_upstreams("claude", current_config, unsafe_mode=False)

        servers = result.get("mcpServers", {})
        assert "ralph" in servers
        assert servers["ralph"]["url"] == "http://127.0.0.1:9999/mcp"
        assert "github" not in servers

    def test_unsafe_mode_true_merges_existing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """unsafe_mode=True merges existing upstreams with the ralph entry."""
        monkeypatch.setenv("HOME", str(tmp_path))

        (tmp_path / ".claude.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "github": {
                            "type": "http",
                            "url": "https://api.example.com/mcp/",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )

        current_config = {
            "mcpServers": {"ralph": {"type": "http", "url": "http://127.0.0.1:9999/mcp"}}
        }

        result = merge_existing_upstreams("claude", current_config, unsafe_mode=True)

        servers = result.get("mcpServers", {})
        assert "ralph" in servers
        assert servers["ralph"]["url"] == "http://127.0.0.1:9999/mcp"
        assert "github" in servers
        assert servers["github"]["url"] == "https://api.example.com/mcp/"

    @pytest.mark.parametrize(
        "agent_name,current_config_factory",
        [
            # claude/agy/nanocoder: use mcpServers
            (
                "claude",
                lambda: {
                    "mcpServers": {"ralph": {"type": "http", "url": "http://127.0.0.1:9999/mcp"}}
                },
            ),
            (
                "agy",
                lambda: {"mcpServers": {"ralph": {"serverUrl": "http://127.0.0.1:9999/mcp"}}},
            ),
            (
                "nanocoder",
                lambda: {
                    "mcpServers": {
                        "ralph": {"transport": "http", "url": "http://127.0.0.1:9999/mcp"}
                    }
                },
            ),
            # opencode: use mcp key with native fields
            (
                "opencode",
                lambda: {
                    "mcp": {
                        "ralph": {
                            "type": "remote",
                            "url": "http://127.0.0.1:9999/mcp",
                            "enabled": True,
                            "timeout": 90000,
                        }
                    }
                },
            ),
            # codex: use mcp_servers.X keys (TOML-style)
            (
                "codex",
                lambda: {
                    "mcp_servers.github": {
                        "url": "https://api.example.com/mcp/",
                        "enabled": True,
                    },
                    "mcp_servers.ralph": {
                        "url": "http://127.0.0.1:9999/mcp",
                        "enabled": True,
                    },
                },
            ),
        ],
    )
    def test_helper_handles_each_supported_agent_path_layout(
        self,
        agent_name: str,
        current_config_factory: callable,
    ) -> None:
        """Smoke test: merge_existing_upstreams is callable for all 5 supported agents.

        Each agent uses its native config format:
        - claude/agy/nanocoder: {"mcpServers": {"<name>": {...}}}
        - opencode: {"mcp": {"<name>": {"type", "url", "enabled", "timeout", ...}}}
        - codex: {"mcp_servers.X": {...}}  (TOML-style keys)
        """
        current_config = current_config_factory()

        result = merge_existing_upstreams(agent_name, current_config, unsafe_mode=False)

        assert isinstance(result, dict)
        if agent_name in ("claude", "agy", "nanocoder"):
            assert "mcpServers" in result
            assert "ralph" in result["mcpServers"]
        elif agent_name == "opencode":
            assert "mcp" in result
            assert "ralph" in result["mcp"]
        elif agent_name == "codex":
            # Codex returns flat dict with mcp_servers.X keys
            assert "mcp_servers.ralph" in result

    def test_opencode_preserves_native_fields(
        self,
    ) -> None:
        """OpenCode entries must preserve all native fields (type, url, enabled, timeout)."""
        current_config = {
            "mcp": {
                "github": {
                    "type": "http",
                    "url": "https://existing.invalid/sse",
                    "enabled": True,
                    "timeout": 90000,
                },
                "ralph": {
                    "type": "remote",
                    "url": "http://127.0.0.1:9999/sse",
                    "enabled": True,
                    "timeout": 90000,
                },
            }
        }

        result = merge_existing_upstreams("opencode", current_config, unsafe_mode=True)

        assert result["mcp"]["github"]["type"] == "http"
        assert result["mcp"]["github"]["url"] == "https://existing.invalid/sse"
        assert result["mcp"]["github"]["enabled"] is True
        assert result["mcp"]["github"]["timeout"] == 90000
        assert result["mcp"]["ralph"]["type"] == "remote"
        assert result["mcp"]["ralph"]["url"] == "http://127.0.0.1:9999/sse"
        assert result["mcp"]["ralph"]["enabled"] is True
        assert result["mcp"]["ralph"]["timeout"] == 90000

    def test_opencode_unsafe_mode_false_returns_only_ralph(
        self,
    ) -> None:
        """When unsafe_mode=False, OpenCode result contains only the ralph entry."""
        current_config = {
            "mcp": {
                "github": {
                    "type": "http",
                    "url": "https://existing.invalid/sse",
                    "enabled": True,
                    "timeout": 90000,
                },
                "ralph": {
                    "type": "remote",
                    "url": "http://127.0.0.1:9999/sse",
                    "enabled": True,
                    "timeout": 90000,
                },
            }
        }

        result = merge_existing_upstreams("opencode", current_config, unsafe_mode=False)

        assert "mcp" in result
        assert "ralph" in result["mcp"]
        assert "github" not in result["mcp"]

    def test_codex_preserves_toml_style_keys(
        self,
    ) -> None:
        """Codex entries use TOML-style mcp_servers.X keys (not mcpServers)."""
        current_config = {
            "mcp_servers.github": {
                "url": "https://existing.invalid/sse",
                "enabled": True,
            },
            "mcp_servers.ralph": {
                "url": "http://127.0.0.1:9999/sse",
                "enabled": True,
            },
        }

        result = merge_existing_upstreams("codex", current_config, unsafe_mode=True)

        assert "mcp_servers.github" in result
        assert "mcp_servers.ralph" in result
        assert "mcpServers" not in result
        # Ralph must NOT leak into a top-level 'ralph' key - it must stay under
        # the proper mcp_servers.ralph TOML key. This catches the bug where
        # result[RALPH_MCP_SERVER_NAME] was used instead of result[codex_ralph_key].
        assert "ralph" not in result, (
            f"Ralph entry leaked to top-level 'ralph' key. Result keys: {list(result.keys())}"
        )
        assert result["mcp_servers.github"]["url"] == "https://existing.invalid/sse"
        assert result["mcp_servers.ralph"]["url"] == "http://127.0.0.1:9999/sse"

    def test_codex_unsafe_mode_false_returns_only_ralph(
        self,
    ) -> None:
        """When unsafe_mode=False, Codex result contains only the ralph entry."""
        current_config = {
            "mcp_servers.github": {
                "url": "https://existing.invalid/sse",
                "enabled": True,
            },
            "mcp_servers.ralph": {
                "url": "http://127.0.0.1:9999/sse",
                "enabled": True,
            },
        }

        result = merge_existing_upstreams("codex", current_config, unsafe_mode=False)

        assert "mcp_servers.ralph" in result
        assert "mcp_servers.github" not in result
