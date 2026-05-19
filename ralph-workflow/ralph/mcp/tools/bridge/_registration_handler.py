"""RegistrationHandler protocol for MCP tool handler functions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._types import JsonObject


class RegistrationHandler(Protocol):
    """Callable protocol for MCP tool handler functions registered in the tool bridge."""

    def __call__(
        self,
        host_session: object | None,
        workspace: object | None,
        params: JsonObject,
    ) -> object: ...
