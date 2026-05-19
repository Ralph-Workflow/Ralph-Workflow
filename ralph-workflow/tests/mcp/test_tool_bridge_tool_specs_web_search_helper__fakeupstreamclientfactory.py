from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

from ralph.mcp.upstream.models import UpstreamTool

if TYPE_CHECKING:
    from ralph.mcp.upstream.config import (
            UpstreamMcpServer,
        )

class _FakeUpstreamClientFactory:
    _tools: list[UpstreamTool]

    def __init__(self, tools: list[dict[str, object]]) -> None:
        result: list[UpstreamTool] = []
        for t in tools:
            name = cast("str", t["name"])
            desc_raw = t.get("description", "")
            desc = str(desc_raw) if desc_raw else ""
            input_schema_raw = t.get("inputSchema", {})
            input_schema = cast("dict[str, object]", input_schema_raw)
            result.append(UpstreamTool(name=name, description=desc, input_schema=input_schema))
        self._tools = result

    def __call__(self, server: UpstreamMcpServer) -> MagicMock:
        mock = MagicMock()
        object.__setattr__(mock.list_tools, "return_value", self._tools)
        return mock
