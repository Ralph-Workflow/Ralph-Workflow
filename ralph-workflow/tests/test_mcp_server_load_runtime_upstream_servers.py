"""Integration tests for the standalone Python MCP server runtime."""

# Property A: there is no alternate FastMCP path. The single production
# _FallbackStandaloneServer (via build_standalone_http_server) is the
# only server construction surface. This test pins the shipped path.

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

# Config imports for multimodal tests
from ralph.config.mcp_models import McpConfig, McpServerSpec
from ralph.mcp.protocol import startup
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import runtime as server_runtime
from ralph.mcp.server.runtime import load_runtime_upstream_servers
from ralph.mcp.tools.names import custom_proxy_tool_name
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UPSTREAM_MCP_TOOL_CATALOG_ENV,
    UpstreamMcpServer,
    serialize_upstream_mcp_servers,
    serialize_upstream_tool_catalog,
)
from ralph.mcp.upstream.registry import UpstreamRegistry
from ralph.mcp.upstream.upstream_tool import UpstreamTool
from ralph.mcp.upstream.validation import UpstreamValidationError
from tests._support.typed_accessors import (
    must_dict_list,
    must_mapping,
    must_str,
)

if TYPE_CHECKING:
    from collections.abc import Callable


# Lazy imports for multimodal tests that require optional dependencies
# These are only available when the multimodal feature is fully configured
_lazy_imports: dict[str, object] = {}

HTTP_OK = 200
HTTP_ACCEPTED = 202


@pytest.fixture(autouse=True)
def _isolate_from_upstream_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Prevent real upstream MCP servers (configured in dev env) from being
    # loaded during tests. Each test provides its own upstream config if needed.
    monkeypatch.delenv(UPSTREAM_MCP_CONFIG_ENV, raising=False)
    monkeypatch.delenv(UPSTREAM_MCP_TOOL_CATALOG_ENV, raising=False)


def _session(run_id: str = "run-1", capabilities: set[str] | None = None) -> AgentSession:
    return AgentSession(
        session_id=f"session-{run_id}",
        run_id=run_id,
        drain="development",
        capabilities=capabilities
        or {
            "RunReportProgress",
            "ArtifactSubmit",
            "EnvRead",
            "WorkspaceRead",
        },
    )


def _http_call(
    endpoint: str, method: str, params: dict[str, object] | None = None, *, msg_id: int = 1
) -> dict[str, object]:
    target = startup.parse_http_endpoint(endpoint)
    return startup.post_http_jsonrpc(
        target,
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params or {},
        },
    )


@pytest.mark.parametrize(
    ("builder_name", "extras_factory"),
    [
        (
            "build_standalone_http_server",
            lambda mcp_config: {"extras": server_runtime.McpServerExtras(mcp_config=mcp_config)},
        ),
    ],
)
@pytest.mark.parametrize(
    ("server_name", "spec", "upstream"),
    [
        (
            "custom-http",
            McpServerSpec(name="custom-http", transport="http", url="http://unused"),
            UpstreamMcpServer(name="custom-http", transport="http", url="http://unused"),
        ),
        (
            "custom-stdio",
            McpServerSpec(name="custom-stdio", transport="stdio", command="custom-mcp"),
            UpstreamMcpServer(name="custom-stdio", transport="stdio", command="custom-mcp"),
        ),
    ],
)
def test_mcp_server_builders_raise_when_any_configured_upstream_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    builder_name: str,
    extras_factory: Callable[[McpConfig], dict[str, object]],
    server_name: str,
    spec: McpServerSpec,
    upstream: UpstreamMcpServer,
) -> None:
    mcp_config = McpConfig(mcp_servers={server_name: spec})

    def fake_build(
        servers: object,
        *,
        client_factory: object | None = None,
        on_unreachable: str = "raise",
    ) -> UpstreamRegistry:
        del servers, client_factory
        if on_unreachable == "warn_and_skip":
            return UpstreamRegistry([], {})
        raise UpstreamValidationError(
            f"upstream MCP server '{server_name}' is unreachable: server unreachable"
        )

    monkeypatch.setattr(server_runtime, "load_runtime_upstream_servers", lambda cfg: (upstream,))
    monkeypatch.setattr(server_runtime.UpstreamRegistry, "build", fake_build)

    builder = getattr(server_runtime, builder_name)
    kwargs = must_mapping(extras_factory(mcp_config))

    with pytest.raises(UpstreamValidationError, match=server_name):
        builder(tmp_path, **kwargs)


def test_build_standalone_http_server_uses_cached_upstream_tool_catalog_without_eager_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = McpConfig(
        mcp_servers={"docs": McpServerSpec(name="docs", transport="http", url="http://unused")}
    )
    monkeypatch.setenv(
        UPSTREAM_MCP_TOOL_CATALOG_ENV,
        serialize_upstream_tool_catalog(
            {
                "docs": [
                    UpstreamTool(
                        name="ping",
                        description="Ping",
                        input_schema={"type": "object"},
                    )
                ]
            }
        ),
    )

    def fail_build(*args: object, **kwargs: object) -> UpstreamRegistry:
        del args, kwargs
        raise AssertionError("eager upstream probe should not run when tool catalog is cached")

    monkeypatch.setattr(server_runtime.UpstreamRegistry, "build", fail_build)

    server = server_runtime.build_standalone_http_server(
        tmp_path,
        extras=server_runtime.McpServerExtras(mcp_config=config),
    )

    tools_response, _state = server._mcp_server._handle_tools_list(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=1, params={})
    )
    assert tools_response is not None
    result = must_mapping(tools_response.result)
    tools = must_dict_list(result["tools"])
    tool_names = {must_str(tool["name"]) for tool in tools}
    assert custom_proxy_tool_name("docs", "ping") in tool_names


# =============================================================================
# Image content serialization tests (Task 3)
# =============================================================================


class TestLoadRuntimeUpstreamServers:
    def test_returns_empty_when_env_not_set(self) -> None:

        result = load_runtime_upstream_servers(McpConfig(), env={})
        assert result == ()

    def test_env_servers_present_when_env_set(self) -> None:

        srv = UpstreamMcpServer(name="env-srv", transport="http", url="http://localhost:9")
        serialized = serialize_upstream_mcp_servers([srv])
        result = load_runtime_upstream_servers(
            McpConfig(), env={UPSTREAM_MCP_CONFIG_ENV: serialized}
        )
        assert any(s.name == "env-srv" for s in result)

    def test_both_env_and_toml_servers_included_when_names_differ(self) -> None:

        env_srv = UpstreamMcpServer(name="env-srv", transport="http", url="http://env:9")
        serialized = serialize_upstream_mcp_servers([env_srv])
        toml_spec = McpServerSpec(name="toml-srv", transport="http", url="http://toml:9")
        config = McpConfig(mcp_servers={"toml-srv": toml_spec})
        result = load_runtime_upstream_servers(config, env={UPSTREAM_MCP_CONFIG_ENV: serialized})
        assert {s.name for s in result} == {"env-srv", "toml-srv"}

    def test_toml_server_overwrites_env_server_on_name_collision(self) -> None:

        shared = "shared-srv"
        env_srv = UpstreamMcpServer(name=shared, transport="http", url="http://env:9")
        serialized = serialize_upstream_mcp_servers([env_srv])
        toml_spec = McpServerSpec(name=shared, transport="http", url="http://toml:9")
        config = McpConfig(mcp_servers={shared: toml_spec})
        result = load_runtime_upstream_servers(config, env={UPSTREAM_MCP_CONFIG_ENV: serialized})
        assert len(result) == 1
        assert result[0].url == "http://toml:9"

    def test_toml_server_collision_preserves_custom_origin(self) -> None:

        shared = "shared-srv"
        env_srv = UpstreamMcpServer(
            name=shared,
            transport="http",
            url="http://env:9",
            origin="agent_upstream",
        )
        serialized = serialize_upstream_mcp_servers([env_srv])
        toml_spec = McpServerSpec(name=shared, transport="http", url="http://toml:9")
        config = McpConfig(mcp_servers={shared: toml_spec})

        result = load_runtime_upstream_servers(config, env={UPSTREAM_MCP_CONFIG_ENV: serialized})

        assert len(result) == 1
        assert result[0].url == "http://toml:9"
        assert result[0].origin == "custom"
