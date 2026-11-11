from __future__ import annotations

import pytest

from ralph.config.mcp_models import McpConfig, WebVisitConfig
from ralph.mcp.protocol.capability_mapping import Capability, SessionDrain
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.tools.bridge import ToolBridge, build_ralph_tool_registry
from ralph.mcp.tools.names import upstream_proxy_tool_name
from ralph.mcp.upstream.models import UpstreamTool
from ralph.mcp.upstream.registry import ProxiedTool, UpstreamRegistry
from ralph.prompts.template_variables import DEFAULT_CAPABILITIES


def _make_session(drain_str: str) -> AgentSession:
    session_drain = SessionDrain(drain_str)
    default_caps = DEFAULT_CAPABILITIES.get(session_drain, ())
    return AgentSession(
        session_id=f"test-{drain_str}",
        run_id="run-1",
        drain=drain_str,
        capabilities={cap.value for cap in default_caps},
    )


def _visit_registry(session: AgentSession) -> ToolBridge:
    mcp_config = McpConfig(web_visit=WebVisitConfig(enabled=True))
    return build_ralph_tool_registry(session, workspace=None, mcp_config=mcp_config)


_COMMIT_CLASS_DRAINS = (
    SessionDrain.DEVELOPMENT_COMMIT,
    SessionDrain.REVIEW_COMMIT,
    SessionDrain.COMMIT,
)

_NON_COMMIT_DRAINS = [d.value for d in SessionDrain if d not in _COMMIT_CLASS_DRAINS]


class TestUpstreamToolPhaseVisibility:
    @pytest.mark.parametrize("drain_str", [d.value for d in SessionDrain])
    def test_upstream_proxy_listed_for_drain(
        self,
        drain_str: str,
    ) -> None:
        session_drain = SessionDrain(drain_str)
        default_caps = DEFAULT_CAPABILITIES.get(session_drain, ())
        session = _make_session(drain_str)

        proxy_alias = upstream_proxy_tool_name("fake_crawl", "fake_tool")
        fake_proxied = ProxiedTool(
            alias=proxy_alias,
            server_name="fake_crawl",
            tool=UpstreamTool(
                name="fake_tool",
                description="A fake upstream tool",
                input_schema={"type": "object", "properties": {}},
            ),
        )
        upstream_registry = UpstreamRegistry([fake_proxied], clients={})

        mcp_config = McpConfig(web_visit=WebVisitConfig(enabled=True))
        registry = build_ralph_tool_registry(
            session,
            workspace=None,
            mcp_config=mcp_config,
            upstream_registry=upstream_registry,
        )
        tool_names = {t.name for t in registry.list_definitions()}

        has_upstream_cap = Capability.UPSTREAM_TOOL_USE in default_caps
        if has_upstream_cap:
            assert proxy_alias in tool_names, (
                f"upstream proxy {proxy_alias} not in tools for drain {drain_str} "
                f"(which has UPSTREAM_TOOL_USE); got: {sorted(tool_names)}"
            )
        else:
            assert proxy_alias not in tool_names, (
                f"upstream proxy {proxy_alias} unexpectedly visible for drain {drain_str}; "
                f"got: {sorted(tool_names)}"
            )
