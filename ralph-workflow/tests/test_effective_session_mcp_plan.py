"""Black-box contract tests for canonical effective session MCP resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.session_plan import resolve_effective_session_mcp_plan
from ralph.mcp.upstream.config import UpstreamMcpServer

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_effective_session_plan_keeps_custom_and_agent_inventory_separate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    custom = UpstreamMcpServer(
        name="docs-mcp",
        transport="stdio",
        command="npx",
        args=("-y", "@arabold/docs-mcp-server@latest"),
        origin="custom",
    )
    native = UpstreamMcpServer(
        name="memory",
        transport="stdio",
        command="native-memory",
        origin="agent_upstream",
    )
    monkeypatch.setattr("ralph.mcp.session_plan.mcp_toml_as_upstreams", lambda _p: (custom,))

    plan = resolve_effective_session_mcp_plan(
        tmp_path,
        agent_upstream_servers=(native,),
    )

    assert plan.custom_servers == (custom,)
    assert plan.agent_upstream_servers == (native,)
    assert tuple(server.name for server in plan.effective_servers) == ("memory", "docs-mcp")
    assert plan.provider_visible_server_names == ("ralph",)


def test_effective_session_plan_prefers_custom_server_on_name_collision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    custom = UpstreamMcpServer(
        name="docs-mcp",
        transport="stdio",
        command="npx",
        args=("-y", "@arabold/docs-mcp-server@latest"),
        origin="custom",
    )
    native = UpstreamMcpServer(
        name="docs-mcp",
        transport="stdio",
        command="native-docs",
        origin="agent_upstream",
    )
    monkeypatch.setattr("ralph.mcp.session_plan.mcp_toml_as_upstreams", lambda _p: (custom,))

    plan = resolve_effective_session_mcp_plan(
        tmp_path,
        agent_upstream_servers=(native,),
    )

    assert len(plan.effective_servers) == 1
    assert plan.effective_servers[0].name == "docs-mcp"
    assert plan.effective_servers[0].origin == "custom"
    assert plan.effective_servers[0].command == "npx"
