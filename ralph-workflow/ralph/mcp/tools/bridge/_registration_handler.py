from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._types import JsonObject


class RegistrationHandler(Protocol):
    def __call__(
        self,
        host_session: object | None,
        workspace: object | None,
        params: JsonObject,
    ) -> object: ...
