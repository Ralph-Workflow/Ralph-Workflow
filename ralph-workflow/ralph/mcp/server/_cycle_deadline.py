"""Cycle-deadline submission notice carried on every MCP tool result.

Past the plan-to-final-commit cycle's warning point, ``development_result``
validation hardens: a ``completed`` result must carry ``## Plan Items Proven``,
and a ``partial`` or ``failed`` result must carry ``## Incomplete Work`` with a
stable ID, a ``Reason:``, and an ``Evidence:`` per item (see
:mod:`ralph.mcp.artifacts.markdown.specs.development_result`). That gate reads
the run's own clock rather than anything the agent declares — deliberately, so
staying silent cannot clear it — which is exactly why the agent has to be told
the rules changed under it.

The prompt appendix alone cannot do the telling. It is computed when the prompt
is materialized, so a session that STARTS before the warning point and runs
through it never receives one; under the bundled defaults (a 120-minute cycle
warning at 96 minutes, a 55-minute session cap) that crossing session is the
common case, and it is precisely the session whose submission meets the gate.
Riding on tool results also survives compaction, which drops the appendix.

The notice deliberately carries no countdown and no routing detail. The
deadline is enforced at routing boundaries, so it never interrupts a running
session; remaining-minutes text gets read as the agent's own clock and buys
early exits and fabricated ``completed`` results — the very outcome the gate
exists to catch. Winding down is the session wrap-up nag's job (see
:mod:`ralph.mcp.server._session_wrapup`), and that nag is the only stop signal.

The warning point is published once per invocation as a wall-clock epoch in the
environment the MCP server subprocess inherits (see
:class:`ralph.mcp.protocol.env.McpEnvVar`); an epoch rather than a duration
because monotonic clocks are not comparable across processes. Both this notice
and the validation gate resolve it through
:func:`ralph.mcp.protocol.cycle_deadline_env.cycle_warning_is_active`, so the
two cannot disagree about whether the agent was warned.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Protocol

from ralph.mcp.protocol.cycle_deadline_env import cycle_warning_is_active

if TYPE_CHECKING:
    from ralph.mcp.websearch.secrets import EnvGetter

__all__ = ["CycleDeadlineNotifier", "cycle_deadline_notice"]

#: Fixed text: what changed about this agent's submission, and nothing else.
_GATE_NOTICE = (
    "⚠️ Cycle timebox passed — this does not shorten your session; keep working "
    "until your session wrap-up notice. It does change how your development_result "
    "validates: `completed` must carry `## Plan Items Proven`; `partial` or `failed` "
    "must carry `## Incomplete Work` with a stable ID, `Reason:`, and `Evidence:` per "
    "item. Enforced from the run's clock, not from anything you declare."
)


class _EpochClock(Protocol):
    def time(self) -> float: ...


class _SystemEpochClock:
    """Wall-clock source; injected so the notice is deterministic in tests."""

    def time(self) -> float:
        return time.time()


def cycle_deadline_notice(
    *,
    now_epoch: float,
    env_getter: EnvGetter = os.environ.get,
) -> str | None:
    """Return the submission-requirements notice, or ``None`` when it is not due.

    ``None`` is returned while the warning point is still ahead, and whenever no
    warning point was published at all (no cycle timebox is configured, or this
    invocation is not inside a guarded cycle) — the notice must not describe a
    gate that cannot fire.
    """
    if not cycle_warning_is_active(now_epoch=now_epoch, env_getter=env_getter):
        return None
    return _GATE_NOTICE


class CycleDeadlineNotifier:
    """Produces the cycle-deadline notice from the published environment.

    The environment is read on every call rather than cached at construction
    so the notice reflects the environment this process was given, however
    late the first tool call arrives. Note the subprocess environment is a
    snapshot taken at spawn: a later publication by the pipeline reaches this
    process only when the restart-aware bridge respawns it. That is sound
    because the bridge is per-invocation and the warning point is fixed for an
    invocation's lifetime.
    """

    def __init__(
        self,
        clock: _EpochClock | None = None,
        env_getter: EnvGetter = os.environ.get,
    ) -> None:
        self._clock = clock or _SystemEpochClock()
        self._env_getter = env_getter

    def notice(self) -> str | None:
        """Return the current notice, or ``None`` when none is due."""
        return cycle_deadline_notice(
            now_epoch=self._clock.time(),
            env_getter=self._env_getter,
        )
