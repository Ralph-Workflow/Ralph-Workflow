"""Filesystem abstraction exports.

Use ``Workspace`` as the protocol shared by production and test code,
``FsWorkspace`` for real filesystem access, and ``MemoryWorkspace`` for tests
that need an in-memory implementation.

The :func:`workspace_context` context manager and :class:`WorkspaceContext`
bundle let callers switch the active agent context to a target worktree
inside a ``with`` block and restore the caller's resources byte-identical
on exit.
"""

from ralph.workspace.context import WorkspaceContext, workspace_context
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.protocol import Workspace, WorkspaceSnapshot
from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

__all__ = [
    "FsWorkspace",
    "MemoryWorkspace",
    "Workspace",
    "WorkspaceContext",
    "WorkspaceScope",
    "WorkspaceSnapshot",
    "resolve_workspace_scope",
    "workspace_context",
]
