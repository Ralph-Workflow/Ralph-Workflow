"""Outcome vocabulary for the parent's wait on a freshly spawned MCP child.

``StartupVerdict`` is the closed set of outcomes
:meth:`ralph.mcp.server._startup_watch.StartupWatch.check` can report: the
child is still booting or still inside its startup envelope, the boot
allowance expired, or the startup envelope expired.

The enum lives in its own module so ``_startup_watch`` keeps a single
top-level class (the repo-structure policy allows one public top-level class
per module). It is re-exported from :mod:`ralph.mcp.server._startup_watch`, so
every existing importer keeps working unchanged.
"""

from __future__ import annotations

from enum import StrEnum


class StartupVerdict(StrEnum):
    """Outcome of one :meth:`ralph.mcp.server._startup_watch.StartupWatch.check`."""

    WAITING = "waiting"
    BOOT_DEADLINE_EXCEEDED = "boot_deadline_exceeded"
    ENVELOPE_DEADLINE_EXCEEDED = "startup_deadline_exceeded"


__all__ = ["StartupVerdict"]
