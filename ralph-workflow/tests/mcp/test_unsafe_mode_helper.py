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
            "mcpServers": {
                "ralph": {"type": "http", "url": "http://127.0.0.1:9999/mcp"}
            }
        }

        result = merge_existing_upstreams(
            "claude", current_config, unsafe_mode=False
        )

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
            "mcpServers": {
                "ralph": {"type": "http", "url": "http://127.0.0.1:9999/mcp"}
            }
        }

        result = merge_existing_upstreams(
            "claude", current_config, unsafe_mode=True
        )

        servers = result.get("mcpServers", {})
        assert "ralph" in servers
        assert servers["ralph"]["url"] == "http://127.0.0.1:9999/mcp"
        assert "github" in servers
        assert servers["github"]["url"] == "https://api.example.com/mcp/"

    @pytest.mark.parametrize(
        "agent_name",
        ["claude", "agy", "nanocoder", "opencode", "codex"],
    )
    def test_helper_handles_each_supported_agent_path_layout(
        self,
        agent_name: str,
    ) -> None:
        """Smoke test: merge_existing_upstreams is callable for all 5 supported agents."""
        current_config = {
            "mcpServers": {
                "ralph": {"type": "http", "url": "http://127.0.0.1:9999/mcp"}
            }
        }

        result = merge_existing_upstreams(
            agent_name, current_config, unsafe_mode=False
        )

        assert isinstance(result, dict)
        assert "mcpServers" in result
        assert "ralph" in result["mcpServers"]
