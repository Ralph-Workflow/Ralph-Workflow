from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._types import JsonObject
    from ralph.mcp.upstream.client import HasMediaManifest
    from ralph.mcp.upstream.registry import UpstreamRegistry
    from ralph.workspace.protocol import Workspace


class UpstreamProxyHandler:
    def __init__(self, alias: str, upstream_registry: UpstreamRegistry) -> None:
        self._alias = alias
        self._upstream_registry = upstream_registry

    def __call__(
        self,
        host_session: object | None,
        workspace: object | None,
        params: JsonObject,
    ) -> object:
        session = cast(
            "HasMediaManifest | None",
            host_session if host_session is not None else None,
        )
        ws = cast("Workspace | None", workspace if workspace is not None else None)
        result = self._upstream_registry.call_tool(
            self._alias,
            params,
            session=cast("HasMediaManifest | None", host_session),
            workspace=cast("Workspace | None", workspace),
        )
        if ws is not None and host_session is not None and isinstance(result, dict):
            from ralph.mcp.tools.workspace import persist_upstream_media_artifacts

            persist_upstream_media_artifacts(result, host_session, ws)
        return result
