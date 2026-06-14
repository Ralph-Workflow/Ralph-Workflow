"""UpstreamProxyHandler class."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._types import JsonObject
    from ralph.mcp.upstream.client import HasMediaManifest
    from ralph.mcp.upstream.registry import UpstreamRegistry


class UpstreamProxyHandler:
    """Proxy handler that forwards tool calls to an upstream MCP registry."""

    def __init__(self, alias: str, upstream_registry: UpstreamRegistry) -> None:
        self._alias = alias
        self._upstream_registry = upstream_registry

    def __call__(
        self,
        host_session: object | None,
        workspace: object | None,
        params: JsonObject,
    ) -> object:
        # The activity sink is intentionally NOT invoked here. The
        # single emission point for every ``tools/call`` (in-process or
        # upstream-proxied) is ``McpServer._handle_tools_call``, which
        # calls ``self._invoke_activity_sinks(tool_name)`` before
        # dispatch. Invoking the sink inside the proxy as well would
        # double-count upstream-proxied calls on the ``mcp_tool``
        # channel and break per-channel accounting. The upstream proxy
        # is therefore a pure pass-through: it forwards the call and
        # returns the result, while the server boundary records activity
        # exactly once per logical call.
        result = self._upstream_registry.call_tool(
            self._alias,
            params,
            session=cast("HasMediaManifest | None", host_session),
        )
        if workspace is not None and host_session is not None and isinstance(result, dict):
            mod = import_module("ralph.mcp.tools.workspace")
            mod.persist_upstream_media_artifacts(result, host_session, workspace)
        return result
