from __future__ import annotations

from typing import Protocol, cast, runtime_checkable

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


def as_tool_handler(handler: object) -> ToolHandler:
    if not callable(handler):
        msg = f"expected callable tool handler, got {type(handler).__name__}"
        raise TypeError(msg)
    return cast("ToolHandler", handler)
