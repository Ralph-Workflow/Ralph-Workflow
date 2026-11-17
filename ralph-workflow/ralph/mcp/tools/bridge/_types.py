from __future__ import annotations

from typing import Protocol, runtime_checkable

JsonObject = dict[str, object]


@runtime_checkable
class ToolHandler(Protocol):
    def __call__(
        self,
        session: object,
        workspace: object,
        params: JsonObject,
        **kwargs: object,
    ) -> object: ...
