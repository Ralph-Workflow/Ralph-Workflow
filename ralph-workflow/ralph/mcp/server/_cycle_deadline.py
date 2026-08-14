"""Cycle-deadline nag carried on every MCP tool result.

The plan-to-final-commit cycle timebox warns a development agent once 80% of
its budget has elapsed. That warning is appended to the prompt that STARTS an
invocation, which leaves two holes: an agent whose context was compacted loses
it, and an invocation that began before the warning point never sees it at all
even though it can run straight through the deadline (the deadline is enforced
at routing boundaries, so a running invocation is never interrupted).

This module closes both holes the same way the graduated session wrap-up nag
does — by riding along on tool results, which the agent cannot miss and
compaction cannot drop. The deadline instant does not move during a single
invocation, so the pipeline publishes it once as wall-clock epochs in the
environment the MCP server subprocess inherits (see
:class:`ralph.mcp.protocol.env.McpEnvVar`); epochs are used rather than
durations because monotonic clocks are not comparable across processes.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Protocol

from ralph.mcp.protocol.env import (
    CYCLE_DEADLINE_EPOCH_ENV,
    CYCLE_FINALIZATION_TARGET_ENV,
    CYCLE_WARN_EPOCH_ENV,
)

if TYPE_CHECKING:
    from ralph.mcp.websearch.secrets import EnvGetter

__all__ = ["CycleDeadlineNotifier", "cycle_deadline_notice"]

_SECONDS_PER_MINUTE = 60.0


class _EpochClock(Protocol):
    def time(self) -> float: ...


class _SystemEpochClock:
    """Wall-clock source; injected so the notice is deterministic in tests."""

    def time(self) -> float:
        return time.time()


def cycle_deadline_notice(
    *,
    now_epoch: float,
    warn_epoch: float | None,
    deadline_epoch: float | None,
    finalization_target: str | None,
) -> str | None:
    """Return the cycle-deadline nag, or ``None`` when it is not due.

    ``None`` is returned when no deadline was published (no cycle timebox is
    configured, or this invocation is not inside a guarded cycle) and while the
    warning point is still ahead. Past the deadline the remaining minutes floor
    at zero rather than going negative: the cycle is over budget and the next
    development entry is redirected.
    """
    if warn_epoch is None or deadline_epoch is None:
        return None
    if now_epoch < warn_epoch:
        return None
    remaining_minutes = max(0, int((deadline_epoch - now_epoch) // _SECONDS_PER_MINUTE))
    target = finalization_target or "the final commit"
    return (
        f"⚠️ Cycle timebox: ~{remaining_minutes} min of this development cycle's "
        f"budget remain. When it is spent, the next development entry is redirected "
        f"to {target} and you get NO further development iteration. Land the "
        f"highest-value work first, and report anything unfinished as partial or "
        f"failed with a stable ID, a reason, and evidence — never as completed."
    )


def _env_float(name: str, env_getter: EnvGetter) -> float | None:
    raw = env_getter(name)
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw)
    except ValueError:
        return None


class CycleDeadlineNotifier:
    """Produces the cycle-deadline nag from the published environment.

    The environment is read on every call rather than cached at construction
    so a server process that outlives a cycle boundary (the restart-aware
    bridge respawns in place) reflects the current publication.
    """

    def __init__(
        self,
        clock: _EpochClock | None = None,
        env_getter: EnvGetter = os.environ.get,
    ) -> None:
        self._clock = clock or _SystemEpochClock()
        self._env_getter = env_getter

    def notice(self) -> str | None:
        """Return the current nag, or ``None`` when none is due."""
        return cycle_deadline_notice(
            now_epoch=self._clock.time(),
            warn_epoch=_env_float(CYCLE_WARN_EPOCH_ENV, self._env_getter),
            deadline_epoch=_env_float(CYCLE_DEADLINE_EPOCH_ENV, self._env_getter),
            finalization_target=self._env_getter(CYCLE_FINALIZATION_TARGET_ENV) or None,
        )
