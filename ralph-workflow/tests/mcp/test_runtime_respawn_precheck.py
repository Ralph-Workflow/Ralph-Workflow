from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server.runtime import McpServerExtras
from ralph.mcp.upstream.client import HttpUpstreamClient, JsonRpcCaller
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_TOOL_CATALOG_ENV,
    UpstreamMcpServer,
    serialize_upstream_tool_catalog,
)
from ralph.mcp.upstream.models import UpstreamCallError, UpstreamTool

if TYPE_CHECKING:
    from ralph.config.mcp_models import McpConfig
    from ralph.mcp.protocol.session import AgentSession
    from ralph.mcp.upstream.registry import UpstreamClientFactory


def _make_session(
    session_id: str = "test-session",
    run_id: str = "test-run",
    capabilities: set[str] | None = None,
) -> AgentSession:
    from ralph.mcp.protocol.session import AgentSession

    return AgentSession(
        session_id=session_id,
        run_id=run_id,
        drain="standalone",
        capabilities=capabilities or {"mcp"},
    )


def _make_mcp_config() -> McpConfig:
    from ralph.config.mcp_models import McpConfig

    return McpConfig()


def _make_upstream_server(name: str = "remote") -> UpstreamMcpServer:
    return UpstreamMcpServer(name=name, transport="http", url="http://unused.example/mcp")


def _counting_caller(counter: list[int]) -> JsonRpcCaller:

    def caller(method: str, _payload: dict[str, object]) -> dict[str, object]:
        if method == "tools/list":
            counter.append(1)
            return {
                "tools": [
                    {
                        "name": "fake-tool",
                        "description": "fake",
                        "inputSchema": {"type": "object"},
                    }
                ]
            }
        return {}

    return caller


def _make_client_factory(counter: list[int]) -> UpstreamClientFactory:

    def factory(server: UpstreamMcpServer) -> HttpUpstreamClient:
        return HttpUpstreamClient(server, caller=_counting_caller(counter))

    return factory


def test_respawn_without_catalog_raises_before_discovery() -> None:
    counter: list[int] = []
    servers = [_make_upstream_server("remote")]

    with (
        patch(
            "ralph.mcp.server.runtime.load_upstream_mcp_servers",
            return_value=servers,
        ),
        patch(
            "ralph.mcp.upstream.registry.make_upstream_client",
            _make_client_factory(counter),
        ),
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        env: dict[str, str] = {"RALPH_MCP_RESPAWN": "1"}
        with pytest.raises(McpServerError) as exc_info:
            build_standalone_http_server(
                workspace_root=Path("/tmp"),
                extras=McpServerExtras(
                    session=_make_session(),
                    upstream_registry=None,
                    mcp_config=_make_mcp_config(),
                ),
                env=env,
            )

    assert exc_info.value.last_stage == "respawn_precheck"
    assert counter == [], (
        "list_tools must not have been called: the respawn precheck must fire "
        "before UpstreamRegistry.build() runs any network I/O"
    )


def test_respawn_without_catalog_message_mentions_respawn() -> None:
    servers = [_make_upstream_server()]
    with patch(
        "ralph.mcp.server.runtime.load_upstream_mcp_servers",
        return_value=servers,
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        env: dict[str, str] = {"RALPH_MCP_RESPAWN": "1"}
        with pytest.raises(McpServerError) as exc_info:
            build_standalone_http_server(
                workspace_root=Path("/tmp"),
                extras=McpServerExtras(
                    session=_make_session(),
                    upstream_registry=None,
                    mcp_config=_make_mcp_config(),
                ),
                env=env,
            )

    msg = str(exc_info.value)
    assert "respawn" in msg.lower() or "RALPH_MCP_RESPAWN" in msg


def test_respawn_without_catalog_last_stage_is_respawn_precheck() -> None:
    servers = [_make_upstream_server()]
    with patch(
        "ralph.mcp.server.runtime.load_upstream_mcp_servers",
        return_value=servers,
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        env: dict[str, str] = {"RALPH_MCP_RESPAWN": "1"}
        with pytest.raises(McpServerError) as exc_info:
            build_standalone_http_server(
                workspace_root=Path("/tmp"),
                extras=McpServerExtras(
                    session=_make_session(),
                    upstream_registry=None,
                    mcp_config=_make_mcp_config(),
                ),
                env=env,
            )

    assert exc_info.value.last_stage == "respawn_precheck"


def test_respawn_with_catalog_uses_build_from_tool_catalog() -> None:
    counter: list[int] = []
    servers = [_make_upstream_server("remote")]
    catalog_tool = UpstreamTool(name="alpha", description="d")
    catalog: dict[str, list[UpstreamTool]] = {"remote": [catalog_tool]}
    with (
        patch(
            "ralph.mcp.server.runtime.load_upstream_mcp_servers",
            return_value=servers,
        ),
        patch(
            "ralph.mcp.upstream.registry.make_upstream_client",
            _make_client_factory(counter),
        ),
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        env: dict[str, str] = {
            "RALPH_MCP_RESPAWN": "1",
            UPSTREAM_MCP_TOOL_CATALOG_ENV: serialize_upstream_tool_catalog(catalog),
        }
        build_standalone_http_server(
            workspace_root=Path("/tmp"),
            extras=McpServerExtras(
                session=_make_session(),
                upstream_registry=None,
                mcp_config=_make_mcp_config(),
            ),
            env=env,
        )

    assert counter == [], (
        "build_from_tool_catalog must not call list_tools; "
        "a respawn child must do zero network I/O before serving"
    )


def test_respawn_with_empty_upstream_servers_no_error() -> None:
    no_servers: list[UpstreamMcpServer] = []
    with patch(
        "ralph.mcp.server.runtime.load_upstream_mcp_servers",
        return_value=no_servers,
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        env: dict[str, str] = {"RALPH_MCP_RESPAWN": "1"}
        build_standalone_http_server(
            workspace_root=Path("/tmp"),
            extras=McpServerExtras(
                session=_make_session(),
                upstream_registry=None,
                mcp_config=_make_mcp_config(),
            ),
            env=env,
        )


def test_non_respawn_first_start_does_not_trigger_respawn_check() -> None:
    no_servers: list[UpstreamMcpServer] = []
    with patch(
        "ralph.mcp.server.runtime.load_upstream_mcp_servers",
        return_value=no_servers,
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        env: dict[str, str] = {}
        build_standalone_http_server(
            workspace_root=Path("/tmp"),
            extras=McpServerExtras(
                session=_make_session(),
                upstream_registry=None,
                mcp_config=_make_mcp_config(),
            ),
            env=env,
        )


def test_first_start_publishes_to_parent(tmp_path: Path) -> None:
    counter: list[int] = []
    servers = [_make_upstream_server("remote")]
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with (
        patch(
            "ralph.mcp.server.runtime.load_upstream_mcp_servers",
            return_value=servers,
        ),
        patch(
            "ralph.mcp.server.runtime.load_upstream_tool_catalog",
            return_value={},
        ),
        patch(
            "ralph.mcp.upstream.registry.make_upstream_client",
            _make_client_factory(counter),
        ),
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        build_standalone_http_server(
            workspace_root=workspace,
            extras=McpServerExtras(
                session=_make_session(),
                upstream_registry=None,
                mcp_config=_make_mcp_config(),
            ),
            env={},
        )

    catalog_file = workspace / ".agent" / "mcp" / "parent_catalog.json"
    assert catalog_file.is_file()
    payload = json.loads(catalog_file.read_text(encoding="utf-8"))
    assert set(payload) == {"remote"}
    assert [t["name"] for t in payload["remote"]] == ["fake-tool"]


def test_first_start_failure_removes_published_file(tmp_path: Path) -> None:
    servers = [_make_upstream_server("remote")]
    workspace = tmp_path / "ws"
    workspace.mkdir()
    catalog_file = workspace / ".agent" / "mcp" / "parent_catalog.json"
    catalog_file.parent.mkdir(parents=True)
    catalog_file.write_text('{"remote": []}', encoding="utf-8")

    def failing_caller(_method: str, _payload: dict[str, object]) -> dict[str, object]:
        raise UpstreamCallError("boom")

    def failing_factory(_server: UpstreamMcpServer) -> HttpUpstreamClient:
        return HttpUpstreamClient(_server, caller=failing_caller)

    with (
        patch(
            "ralph.mcp.server.runtime.load_upstream_mcp_servers",
            return_value=servers,
        ),
        patch(
            "ralph.mcp.server.runtime.load_upstream_tool_catalog",
            return_value={},
        ),
        patch(
            "ralph.mcp.upstream.registry.make_upstream_client",
            failing_factory,
        ),
        pytest.raises(McpServerError) as exc_info,
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        build_standalone_http_server(
            workspace_root=workspace,
            extras=McpServerExtras(
                session=_make_session(),
                upstream_registry=None,
                mcp_config=_make_mcp_config(),
            ),
            env={},
        )

    assert not catalog_file.exists()
    assert isinstance(exc_info.value, McpServerError)


def test_respawn_with_present_empty_catalog_no_discovery() -> None:
    counter: list[int] = []
    servers = [_make_upstream_server("remote")]

    with (
        patch(
            "ralph.mcp.server.runtime.load_upstream_mcp_servers",
            return_value=servers,
        ),
        patch(
            "ralph.mcp.upstream.registry.make_upstream_client",
            _make_client_factory(counter),
        ),
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        env: dict[str, str] = {
            "RALPH_MCP_RESPAWN": "1",
            UPSTREAM_MCP_TOOL_CATALOG_ENV: "{}",
        }
        build_standalone_http_server(
            workspace_root=Path("/tmp"),
            extras=McpServerExtras(
                session=_make_session(),
                upstream_registry=None,
                mcp_config=_make_mcp_config(),
            ),
            env=env,
        )

    assert counter == [], "list_tools must not be called when catalog is present"


def test_respawn_with_present_empty_server_list_in_catalog_no_discovery() -> None:
    counter: list[int] = []
    servers = [_make_upstream_server("remote")]

    with (
        patch(
            "ralph.mcp.server.runtime.load_upstream_mcp_servers",
            return_value=servers,
        ),
        patch(
            "ralph.mcp.upstream.registry.make_upstream_client",
            _make_client_factory(counter),
        ),
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        env: dict[str, str] = {
            "RALPH_MCP_RESPAWN": "1",
            UPSTREAM_MCP_TOOL_CATALOG_ENV: '{"remote":[]}',
        }
        build_standalone_http_server(
            workspace_root=Path("/tmp"),
            extras=McpServerExtras(
                session=_make_session(),
                upstream_registry=None,
                mcp_config=_make_mcp_config(),
            ),
            env=env,
        )

    assert counter == [], (
        "remote server has zero tools in parent snapshot; "
        "it must be filtered and no probe must occur"
    )


def test_respawn_with_present_partial_catalog_drops_unlisted_server() -> None:
    counter: list[int] = []
    servers = [
        _make_upstream_server("remote"),
        _make_upstream_server("other"),
    ]
    catalog_tool = UpstreamTool(name="alpha", description="d")
    catalog = {"other": [catalog_tool]}

    with (
        patch(
            "ralph.mcp.server.runtime.load_upstream_mcp_servers",
            return_value=servers,
        ),
        patch(
            "ralph.mcp.upstream.registry.make_upstream_client",
            _make_client_factory(counter),
        ),
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        env: dict[str, str] = {
            "RALPH_MCP_RESPAWN": "1",
            UPSTREAM_MCP_TOOL_CATALOG_ENV: serialize_upstream_tool_catalog(catalog),
        }
        build_standalone_http_server(
            workspace_root=Path("/tmp"),
            extras=McpServerExtras(
                session=_make_session(),
                upstream_registry=None,
                mcp_config=_make_mcp_config(),
            ),
            env=env,
        )

    assert counter == [], (
        "build_from_tool_catalog must not call list_tools; "
        "'remote' must be dropped because it is absent from the catalog"
    )


def test_respawn_with_present_corrupt_catalog_raises() -> None:
    servers = [_make_upstream_server("remote")]

    with patch(
        "ralph.mcp.server.runtime.load_upstream_mcp_servers",
        return_value=servers,
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        env: dict[str, str] = {
            "RALPH_MCP_RESPAWN": "1",
            UPSTREAM_MCP_TOOL_CATALOG_ENV: "{not valid json",
        }
        with pytest.raises(McpServerError) as exc_info:
            build_standalone_http_server(
                workspace_root=Path("/tmp"),
                extras=McpServerExtras(
                    session=_make_session(),
                    upstream_registry=None,
                    mcp_config=_make_mcp_config(),
                ),
                env=env,
            )

    assert exc_info.value.last_stage == "parent_catalog_load"


def test_first_start_with_present_empty_catalog_skips_discovery(tmp_path: Path) -> None:
    counter: list[int] = []
    servers = [_make_upstream_server("remote")]
    workspace = tmp_path / "ws"
    workspace.mkdir()

    with (
        patch(
            "ralph.mcp.server.runtime.load_upstream_mcp_servers",
            return_value=servers,
        ),
        patch(
            "ralph.mcp.upstream.registry.make_upstream_client",
            _make_client_factory(counter),
        ),
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        env: dict[str, str] = {
            UPSTREAM_MCP_TOOL_CATALOG_ENV: "{}",
        }
        build_standalone_http_server(
            workspace_root=workspace,
            extras=McpServerExtras(
                session=_make_session(),
                upstream_registry=None,
                mcp_config=_make_mcp_config(),
            ),
            env=env,
        )

    assert counter == [], (
        "present-empty catalog must short-circuit discovery; "
        "list_tools must not be called when the parent has an authoritative empty snapshot"
    )
    catalog_file = workspace / ".agent" / "mcp" / "parent_catalog.json"
    assert not catalog_file.exists(), (
        "present-empty catalog must not publish parent_catalog.json; "
        "the catalog-only path does not run UpstreamRegistry.build()"
    )
