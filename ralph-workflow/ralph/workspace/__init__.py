from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.workspace.context import WorkspaceContext, workspace_context
    from ralph.workspace.fs import FsWorkspace
    from ralph.workspace.memory import MemoryWorkspace
    from ralph.workspace.probe import DefaultWorkspaceChangeProbe, WorkspaceChangeProbe
    from ralph.workspace.protocol import Workspace, WorkspaceSnapshot
    from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

__all__ = [
    "DefaultWorkspaceChangeProbe",
    "FsWorkspace",
    "MemoryWorkspace",
    "Workspace",
    "WorkspaceChangeProbe",
    "WorkspaceContext",
    "WorkspaceScope",
    "WorkspaceSnapshot",
    "resolve_workspace_scope",
    "workspace_context",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "DefaultWorkspaceChangeProbe": ("ralph.workspace.probe", "DefaultWorkspaceChangeProbe"),
    "FsWorkspace": ("ralph.workspace.fs", "FsWorkspace"),
    "MemoryWorkspace": ("ralph.workspace.memory", "MemoryWorkspace"),
    "Workspace": ("ralph.workspace.protocol", "Workspace"),
    "WorkspaceChangeProbe": ("ralph.workspace.probe", "WorkspaceChangeProbe"),
    "WorkspaceContext": ("ralph.workspace.context", "WorkspaceContext"),
    "WorkspaceScope": ("ralph.workspace.scope", "WorkspaceScope"),
    "WorkspaceSnapshot": ("ralph.workspace.protocol", "WorkspaceSnapshot"),
    "resolve_workspace_scope": ("ralph.workspace.scope", "resolve_workspace_scope"),
    "workspace_context": ("ralph.workspace.context", "workspace_context"),
}


def __getattr__(name: str) -> object:
    export = _LAZY_EXPORTS.get(name)
    if export is not None:
        defining_module, attr_name = export
        module = import_module(defining_module)
        value = cast("dict[str, object]", module.__dict__)[attr_name]
        cast("dict[str, object]", globals())[name] = value
        return value

    submodule = f"{__name__}.{name}"
    try:
        return import_module(submodule)
    except ModuleNotFoundError as exc:
        if exc.name != submodule:
            raise
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
