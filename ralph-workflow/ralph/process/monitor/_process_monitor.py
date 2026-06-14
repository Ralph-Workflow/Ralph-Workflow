"""Process monitor protocol for agent-agnostic subagent discovery.

The process monitor is responsible for discovering and classifying processes
in the agent's process tree. It distinguishes the host process (the top-level
agent Ralph launched), spawned subagents (descendants doing delegated work),
and incidental helpers (short-lived shells, the MCP server, tool subprocesses).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable


class ProcessRole(StrEnum):
    """Role of a process in the agent process tree."""

    HOST = "host"
    SPAWNED_SUBAGENT = "spawned_subagent"
    INCIDENTAL_HELPER = "incidental_helper"
    UNKNOWN = "unknown"


@runtime_checkable
class ClassifiedProcess(Protocol):
    """A process with an assigned role and optional metadata."""

    @property
    def pid(self) -> int: ...

    @property
    def role(self) -> ProcessRole: ...

    @property
    def cmdline(self) -> list[str] | None: ...


@runtime_checkable
class ProcessMonitor(Protocol):
    """Agent-agnostic monitor for discovering and classifying subagents.

    The watchdog consumes a ``ProcessMonitor`` implementation via constructor
    injection. The monitor answers two questions:

    1. How many spawned subagents are currently live?
    2. What output streams (if any) are observable for those subagents?

    Implementations may use psutil, OS-specific APIs, or test fakes.
    """

    def live_subagent_count(self) -> int:
        """Return the number of currently live spawned subagents."""
        ...

    def classified_processes(self) -> tuple[ClassifiedProcess, ...]:
        """Return all classified processes in the tree."""
        ...

    def refresh(self) -> None:
        """Refresh the monitor's view of the process tree.

        May be a no-op for implementations that scan on demand.
        """
        ...
