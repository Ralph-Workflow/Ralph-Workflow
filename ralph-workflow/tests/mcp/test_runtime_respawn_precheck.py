"""R2: build_standalone_http_server raises McpServerError before any registry work
when RALPH_MCP_RESPAWN=1 and UPSTREAM_MCP_TOOL_CATALOG is absent.

The contract:
  - is_respawn AND no tool_catalog → McpServerError with last_stage="respawn_precheck"
    BEFORE any UpstreamRegistry.build() (zero list_tools calls to upstreams).
  - is_respawn AND tool_catalog present → UpstreamRegistry.build_from_tool_catalog path
    (zero list_tools calls, no McpServerError).

These are unit tests that mock all network-facing components. They verify the
correct error type, message, and last_stage is raised at the right point in
the call sequence — without assuming implementation internals beyond the
public build_standalone_http_server API.
"""

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    session_id: str = "test-session",
    run_id: str = "test-run",
    capabilities: set[str] | None = None,
) -> AgentSession:
    """Build a minimal real AgentSession with the attributes runtime reads."""
    from ralph.mcp.protocol.session import AgentSession

    return AgentSession(
        session_id=session_id,
        run_id=run_id,
        drain="standalone",
        capabilities=capabilities or {"mcp"},
    )


def _make_mcp_config() -> McpConfig:
    """Build a minimal real McpConfig with all-default fields."""
    from ralph.config.mcp_models import McpConfig

    return McpConfig()


def _make_upstream_server(name: str = "remote") -> UpstreamMcpServer:
    """Build a minimal real upstream server definition."""
    return UpstreamMcpServer(name=name, transport="http", url="http://unused.example/mcp")


def _counting_caller(counter: list[int]) -> JsonRpcCaller:
    """JSON-RPC caller that records every tools/list probe in ``counter``."""

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
    """Client factory producing real HttpUpstreamClients with a counting caller."""

    def factory(server: UpstreamMcpServer) -> HttpUpstreamClient:
        return HttpUpstreamClient(server, caller=_counting_caller(counter))

    return factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_respawn_without_catalog_raises_before_discovery() -> None:
    """RALPH_MCP_RESPAWN=1 with no UPSTREAM_MCP_TOOL_CATALOG must raise
    McpServerError with last_stage='respawn_precheck' BEFORE any list_tools
    call is made on any upstream client."""
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
    """The error message must be informative for an operator to understand the contract violation."""
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
    """last_stage must be 'respawn_precheck' so the supervisor can record the precise stage."""
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
    """When RESPAWN=1 AND UPSTREAM_MCP_TOOL_CATALOG is present,
    build_from_tool_catalog is called (zero list_tools calls)."""
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
    """When RESPAWN=1 but no upstreams are configured (empty list),
    no error should be raised because there is nothing to probe."""
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
    """A first-start (RESPAWN not set) must not trigger the respawn precheck."""
    no_servers: list[UpstreamMcpServer] = []
    with patch(
        "ralph.mcp.server.runtime.load_upstream_mcp_servers",
        return_value=no_servers,
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        env: dict[str, str] = {}  # No RESPAWN — first start path
        build_standalone_http_server(
            workspace_root=Path("/tmp"),
            extras=McpServerExtras(
                session=_make_session(),
                upstream_registry=None,
                mcp_config=_make_mcp_config(),
            ),
            env=env,
        )


# ---------------------------------------------------------------------------
# I1b step 2: first-start publication + failure unlink
# ---------------------------------------------------------------------------


def test_first_start_publishes_to_parent(tmp_path: Path) -> None:
    """First-start discovery must publish the discovered catalog to
    ``.agent/mcp/parent_catalog.json`` so the parent (and respawn) can read it."""
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
    """A failed first-start discovery must unlink any published catalog and
    propagate the typed error so respawn never reads a ghost file."""
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


# ---------------------------------------------------------------------------
# R2 regression: presence-aware catalog detection
# ---------------------------------------------------------------------------


def test_respawn_with_present_empty_catalog_no_discovery() -> None:
    """RALPH_MCP_RESPAWN=1 with UPSTREAM_MCP_TOOL_CATALOG='{}' (present-but-empty)
    must NOT raise; the catalog is present and authoritative, so the child
    must NOT probe upstreams (zero list_tools calls)."""
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
        # Must not raise: catalog is present (env var is SET), even if empty.
        build_standalone_http_server(
            workspace_root=Path("/tmp"),
            extras=McpServerExtras(
                session=_make_session(),
                upstream_registry=None,
                mcp_config=_make_mcp_config(),
            ),
            env=env,
        )

    # The present-empty catalog "{}" means "remote" has zero tools in the
    # parent snapshot; the filter drops "remote" and upstream_servers becomes
    # empty. Zero network I/O is the invariant.
    assert counter == [], "list_tools must not be called when catalog is present"


def test_respawn_with_present_empty_server_list_in_catalog_no_discovery() -> None:
    """RALPH_MCP_RESPAWN=1 with UPSTREAM_MCP_TOOL_CATALOG='{"remote":[]}'
    (present with empty tool list) must NOT raise; "remote" is dropped from
    upstream_servers because its tool list is empty, so no probe occurs."""
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
    """RALPH_MCP_RESPAWN=1 with UPSTREAM_MCP_TOOL_CATALOG='{"other":[...]}'
    must use build_from_tool_catalog for "other" and drop "remote" (not in
    catalog); zero list_tools calls."""
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

    # "remote" is not in catalog, so it is filtered out.
    # "other" is in catalog, so build_from_tool_catalog handles it with zero I/O.
    assert counter == [], (
        "build_from_tool_catalog must not call list_tools; "
        "'remote' must be dropped because it is absent from the catalog"
    )


def test_respawn_with_present_corrupt_catalog_raises() -> None:
    """RALPH_MCP_RESPAWN=1 with malformed UPSTREAM_MCP_TOOL_CATALOG value
    must raise McpServerError with last_stage='parent_catalog_load'
    (fail-closed: malformed env value is a contract violation)."""
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

    # The loader raises with last_stage="parent_catalog_load", which
    # propagates before the respawn precheck can run.
    assert exc_info.value.last_stage == "parent_catalog_load"


def test_first_start_with_present_empty_catalog_skips_discovery(tmp_path: Path) -> None:
    """First start (no RESPAWN) with UPSTREAM_MCP_TOOL_CATALOG='{}' (present-empty)
    must NOT probe upstreams. The parent has an authoritative empty snapshot
    (even on a first start the env var can be set by the test seam), so the
    filter drops every server and the catalog-only path runs with
    upstream_reg=None. No list_tools calls, no parent_catalog.json published.
    Production first starts never have the env var set, but the contract
    must hold for any caller that does: presence-aware filtering is a runtime
    invariant, not a respawn-only one.
    """
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

        # First start: no RALPH_MCP_RESPAWN, but the catalog env var IS set
        # to an empty mapping. The filter drops "remote" because the catalog
        # is empty; the catalog-only path runs with upstream_reg=None.
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

    # Zero list_tools calls: the present-empty catalog is authoritative.
    assert counter == [], (
        "present-empty catalog must short-circuit discovery; "
        "list_tools must not be called when the parent has an authoritative empty snapshot"
    )
    # No parent_catalog.json published: the catalog-only path skips the
    # first-start publish call site.
    catalog_file = workspace / ".agent" / "mcp" / "parent_catalog.json"
    assert not catalog_file.exists(), (
        "present-empty catalog must not publish parent_catalog.json; "
        "the catalog-only path does not run UpstreamRegistry.build()"
    )
