from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from types import ModuleType

    from ralph.mcp.tools.bridge._types import JsonObject


class LazyToolHandler:
    def __init__(
        self,
        *,
        module_name: str,
        handler_name: str,
        session: object,
        workspace: object,
        extra_kwargs: dict[str, object] | None = None,
    ) -> None:
        self._module_name = module_name
        self._handler_name = handler_name
        self._session = session
        self._workspace = workspace
        self._extra_kwargs: dict[str, object] = extra_kwargs if extra_kwargs is not None else {}

    def __call__(
        self,
        host_session: object | None,
        workspace: object | None,
        params: JsonObject,
    ) -> object:
        del host_session, workspace
        loaded: object = self._loader()
        if not callable(loaded):
            raise TypeError(f"tool handler loader returned non-callable: {loaded!r}")
        result: object = loaded(self._session, self._workspace, params, **self._extra_kwargs)
        return result

