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
        # Notify the activity sink BEFORE delegating to the upstream
        # transport so the watchdog's ``mcp_tool`` channel is recorded
        # on the same logical call (success or error). This mirrors the
        # in-process contract from
        # ``ralph.mcp.server._mcp_server.McpServer._handle_tools_call``
        # (line 487) where ``self._invoke_activity_sinks(tool_name)`` is
        # called BEFORE the ``try/except`` that wraps
        # ``self._registry.dispatch``. ``invoke_active_sink`` is itself
        # exception-swallowing and idempotent, so a buggy or absent
        # watchdog binding costs nothing and cannot crash the proxy
        # path. A tool call that raises still produces an evidence
        # signal, matching the in-process behaviour where a wedged tool
        # is still a tool that was called.
        #
        # Imported lazily inside ``__call__`` to avoid a circular
        # import: importing ``ralph.mcp.server._activity_sink`` at
        # module top would pull in ``ralph.mcp.server`` -> ``lifecycle``
        # -> ``protocol.startup`` -> ``tool_contract`` -> ``tools.bridge``
        # -> ``_registry`` -> ``_upstream_proxy_handler`` (us), which
        # fails with a partial-init error during the very first import.
        # The lazy import is a one-time cost on the first dispatch; it
        # also keeps the proxy importable in isolation.
        from ralph.mcp.server._activity_sink import invoke_active_sink  # noqa: PLC0415

        invoke_active_sink(self._alias)
        result = self._upstream_registry.call_tool(
            self._alias,
            params,
            session=cast("HasMediaManifest | None", host_session),
        )
        if workspace is not None and host_session is not None and isinstance(result, dict):
            mod = import_module("ralph.mcp.tools.workspace")
            mod.persist_upstream_media_artifacts(result, host_session, workspace)
        return result
