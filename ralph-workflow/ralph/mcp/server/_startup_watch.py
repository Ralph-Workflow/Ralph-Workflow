"""Deadline bookkeeping for the parent's wait on a freshly spawned MCP child.

A spawned MCP server has two distinct phases with different owners:

* BOOT -- interpreter start and module imports. Host-dependent, and invisible
  to the child's own startup clock (``t0`` in ``run_standalone_server`` is
  taken after the imports).
* ENVELOPE -- the child's own pre-serve work, bounded by the real startup
  envelope the child enforces on itself.

Anchoring one deadline at spawn conflates them: under swap/iowait pressure a
cold boot alone exhausts the envelope even though the child is healthy and
progressing. :class:`StartupWatch` keeps the phases apart. The child's first
recorded stage marks the BOOT -> ENVELOPE transition, so boot gets its own
allowance and the envelope is measured from when the child actually began.

The class is pure: the clock and the stage reader are injected, which keeps
every edge case (never-booting child, slow boot, boot at the edge of the
allowance, legacy spawn-anchored mode) deterministic under test.

:class:`StartupVerdict` -- the outcome vocabulary :meth:`StartupWatch.check`
returns -- is defined in :mod:`ralph.mcp.server._startup_watch_verdict` and
re-exported here, so ``from ralph.mcp.server._startup_watch import
StartupVerdict`` keeps working unchanged.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from ralph.mcp.server._startup_watch_verdict import StartupVerdict

if TYPE_CHECKING:
    from collections.abc import Callable

#: How often the (filesystem-backed) stage file is re-read while the child is
#: still booting. The wait loop itself polls far more often; reading the file
#: on every tick would add avoidable I/O exactly when the host is I/O bound.
STAGE_POLL_INTERVAL_S: Final[float] = 0.25


@dataclass
class StartupWatch:
    """Track a spawned child through BOOT then ENVELOPE under injected time.

    ``boot_allowance_s <= 0`` disables the split: the envelope is anchored at
    spawn, which is the legacy behaviour (and what a caller that supplies no
    boot allowance gets).
    """

    spawn_start: float
    envelope_s: float
    boot_allowance_s: float
    read_stage: Callable[[], str | None]
    clock: Callable[[], float] = time.monotonic
    stage_poll_interval_s: float = STAGE_POLL_INTERVAL_S
    child_started_at: float | None = field(default=None, init=False)
    _next_stage_poll_at: float = field(default=0.0, init=False)

    @property
    def split_phases(self) -> bool:
        """Whether boot is budgeted separately from the envelope."""
        return self.boot_allowance_s > 0

    @property
    def total_budget_s(self) -> float:
        """Upper bound on spawn-to-ready time this watch can ever allow."""
        return self.envelope_s + max(0.0, self.boot_allowance_s)

    def check(self) -> StartupVerdict:
        """Advance the watch to ``clock()`` and report whether to keep waiting."""
        now = self.clock()
        if not self.split_phases:
            if now - self.spawn_start >= self.envelope_s:
                return StartupVerdict.ENVELOPE_DEADLINE_EXCEEDED
            return StartupVerdict.WAITING

        if self.child_started_at is None and now >= self._next_stage_poll_at:
            self._next_stage_poll_at = now + self.stage_poll_interval_s
            if self.read_stage() is not None:
                self.child_started_at = now

        if self.child_started_at is None:
            if now - self.spawn_start >= self.boot_allowance_s:
                return StartupVerdict.BOOT_DEADLINE_EXCEEDED
            return StartupVerdict.WAITING

        if now - self.child_started_at >= self.envelope_s:
            return StartupVerdict.ENVELOPE_DEADLINE_EXCEEDED
        return StartupVerdict.WAITING


__all__ = ["STAGE_POLL_INTERVAL_S", "StartupVerdict", "StartupWatch"]
