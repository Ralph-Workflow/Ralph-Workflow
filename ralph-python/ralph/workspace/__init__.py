"""Ralph workspace package for filesystem abstraction."""

from ralph.workspace.fs import FsWorkspace
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.protocol import Workspace

__all__ = [
    "FsWorkspace",
    "MemoryWorkspace",
    "Workspace",
]
