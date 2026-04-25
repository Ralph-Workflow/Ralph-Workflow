from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import AgentTransport
from ralph.mcp.session_plan import build_session_mcp_plan
from ralph.mcp.upstream.config import UPSTREAM_MCP_CONFIG_ENV, load_upstream_mcp_servers

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return home


def test_session_mcp_plan_derives_web_and_upstream_capabilities_from_live_config(
    isolated_home: Path,
    tmp_path: Path,
) -> None:
    (isolated_home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {"command": "npx", "args": ["-y", "github-mcp"]}
                }
            }
        ),
        encoding="utf-8",
    )
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "mcp.toml").write_text(
        """
[mcp_servers.docs]
transport = "http"
url = "http://docs.example/mcp"

[web_search]
enabled = true

[web_visit]
enabled = true
""".strip(),
        encoding="utf-8",
    )

    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="planning",
        workspace_path=tmp_path,
    )

    assert "web.search" in plan.capabilities
    assert "web.visit" in plan.capabilities
    assert "upstream.tool_use" in plan.capabilities
    assert plan.server_env is not None

    upstreams = load_upstream_mcp_servers(plan.server_env[UPSTREAM_MCP_CONFIG_ENV])
    assert {server.name for server in upstreams} == {"github", "docs"}


def test_session_mcp_plan_omits_web_search_for_commit_even_when_enabled(
    isolated_home: Path,
    tmp_path: Path,
) -> None:
    del isolated_home
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "mcp.toml").write_text(
        """
[web_search]
enabled = true

[web_visit]
enabled = true
""".strip(),
        encoding="utf-8",
    )

    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="commit",
        workspace_path=tmp_path,
    )

    assert "web.search" not in plan.capabilities
    assert "web.visit" in plan.capabilities
