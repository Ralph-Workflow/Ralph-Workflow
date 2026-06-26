"""Process monitor protocol for agent-agnostic subagent discovery.

The process monitor is responsible for discovering and classifying processes
in the agent's process tree. It distinguishes the host process (the top-level
agent Ralph launched), spawned subagents (descendants doing delegated work),
and incidental helpers (short-lived shells, the MCP server, tool subprocesses).
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ._subagent_output_capture import SubagentOutputCapture


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

    Subagent counting contract (R1, Trustworthy Idle Watchdog spec):

        ``spawned_subagent_count()`` (preferred) and the legacy alias
        ``live_subagent_count()`` BOTH return the FILTERED count of
        processes classified as :class:`ProcessRole.SPAWNED_SUBAGENT`
        (the authoritative "real subagent" set, sourced from the
        ``SubagentPidSource`` registered with the monitor and/or the
        transport's role classifier).

        The FILTERED count is the ONLY count the watchdog defers on
        for the ``WAITING_ON_CHILD`` branch and the
        ``CHILDREN_PERSIST_TOO_LONG`` ceiling. The BROADER descendant
        count from ``handle.descendant_snapshot()`` (which includes
        agent-spawned shell helpers like ``npm test``, ``cargo build``,
        ``find /``, MCP server internals, transport spawns) MUST
        NEVER be used for the deferral decision -- counting those as
        ``children`` is the bug that produced the 2365s indefinite
        deferral in the product spec (R3). See
        ``ralph/agents/idle_watchdog/_subagent_identity.py`` for the
        canonical ``SubagentIdentity`` / ``SubagentPidRegistry`` types
        and the audit
        ``ralph/testing/audit_activity_aware_watchdog.subagent_counting_seam``
        that enforces the seam.
    """

    def live_subagent_count(self) -> int:
        """Return the number of currently live spawned subagents.

        Deprecated alias for :meth:`spawned_subagent_count`; both
        methods return the SAME filtered count over
        ``ProcessRole.SPAWNED_SUBAGENT``. New callers should prefer
        ``spawned_subagent_count`` for clarity at the call site. The
        alias is preserved for backward compatibility with existing
        callers in ``_waiting_branch.py`` and ``_activity_methods.py``
        that continue to call ``live_subagent_count``.
        """
        ...

    def spawned_subagent_count(self) -> int:
        """Return the number of currently live spawned subagents (FILTERED count).

        Preferred name for the filtered subagent count. Same return
        value as ``live_subagent_count()`` -- both return the count
        of processes classified as ``ProcessRole.SPAWNED_SUBAGENT``.

        The readers (``_process_reader._corroborate`` and
        ``_pty_line_reader._corroborate``) MUST use this name at
        the call site so the intent -- "count real subagents, not
        the broader descendant tree" -- is unambiguous. The audit
        ``ralph.testing.audit_activity_aware_watchdog`` flags any
        reader that falls back to ``handle.descendant_snapshot()``
        as a regression.
        """
        ...

    def classified_processes(self) -> tuple[ClassifiedProcess, ...]:
        """Return all classified processes in the tree."""
        ...

    def refresh(self) -> None:
        """Refresh the monitor's view of the process tree.

        May be a no-op for implementations that scan on demand.
        """
        ...

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        """Return observable subagent output streams.

        The monitor is responsible for discovering which subagent output
        streams (if any) are observable for the host process it tracks.
        Implementations that cannot observe subagent output return an empty
        mapping so the watchdog degrades gracefully to other channels.
        """
        ...
