"""Filesystem abstraction exports.

Use ``Workspace`` as the protocol shared by production and test code,
``FsWorkspace`` for real filesystem access, and ``MemoryWorkspace`` for tests
that need an in-memory implementation.
"""

from ralph.workspace.fs import FsWorkspace
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.protocol import Workspace
from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

__all__ = [
    "FsWorkspace",
    "MemoryWorkspace",
    "Workspace",
    "WorkspaceScope",
    "resolve_workspace_scope",
]
