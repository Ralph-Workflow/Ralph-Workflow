"""A wall-clock stop the conflict-resolution driver enforces ITSELF.

Every other bound on a resolution round lives inside the call the driver
is blocked on. The session ceiling
(:func:`~ralph.pipeline.conflict_resolution.session.with_session_ceiling`)
is read by ``ralph.agents.invoke``; the idle watchdog evaluates on the
reader loop; the force-cut is delivered by that loop's own thread. Each
of them is a promise the layer below makes to the layer above, and a
promise is not an enforcement: a reader that never pumps, a watchdog
that never evaluates, or a process that cannot be signalled leaves the
driver waiting with a rebase paused mid-replay, no output, and no exit
but SIGKILL.

This module is the driver's own answer, and it depends on nothing below
it: run the attempt on a worker thread, wait exactly as long as the
attempt's share of the deadline allows, and give up on it if it has not
come back. Giving up must leave nothing behind that could still write to
the worktree the caller is about to ``git rebase --abort``, so an
abandoned attempt's agent processes are reaped before control returns.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from loguru import logger

from ralph.process.manager import get_process_manager
from ralph.process.mcp_supervisor import AGENT_PROCESS_LABEL_PREFIX

__all__ = ["HardStop", "call_with_hard_stop"]

#: Runs one resolution attempt under a wall-clock stop:
#: ``(attempt, timeout_seconds) -> ok | None``, where ``None`` means the
#: attempt was abandoned because it outlived its share. Injected so the
#: driver's behaviour on a wedged agent can be proven without wedging one.
type HardStop = Callable[[Callable[[], bool], float], bool | None]

#: Names the worker so an abandoned attempt is identifiable in a stack
#: dump taken from a run that survived one.
_ATTEMPT_THREAD_NAME = "ralph-conflict-resolution-attempt"


def call_with_hard_stop(
    attempt: Callable[[], bool],
    timeout_seconds: float,
) -> bool | None:
    """Run ``attempt``, abandoning it if it outlives ``timeout_seconds``.

    Args:
        attempt: The resolution attempt to run. Its exceptions are
            absorbed: a round that died is a failed round, never a
            failed run.
        timeout_seconds: How long the caller is willing to wait.

    Returns:
        What ``attempt`` returned, ``False`` if it raised, or ``None``
        if it had not returned when the stop expired. ``None`` is the
        only outcome the caller must treat as "the layer below is
        wedged" rather than "this agent could not resolve it".
    """
    live_agents_before = _live_agent_pids()
    outcome: list[bool] = []

    def _run_attempt() -> None:
        try:
            outcome.append(attempt())
        except Exception:
            logger.opt(exception=True).warning(
                "conflict_resolution: the resolution attempt raised; failing the round"
            )

    worker = threading.Thread(target=_run_attempt, name=_ATTEMPT_THREAD_NAME, daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        reaped = _reap_agents_started_since(live_agents_before)
        logger.error(
            "conflict_resolution: the resolution attempt did not return within "
            "{}s and has been abandoned; reaped agent process group(s): {}",
            round(timeout_seconds, 1),
            list(reaped) or "none",
        )
        return None
    return outcome[0] if outcome else False


def _live_agent_pids() -> frozenset[int]:
    """PIDs of the agent subprocesses already running before an attempt.

    Anything under the agent label that is NOT in this set when an
    attempt is abandoned was started BY that attempt, which is what
    makes the reap precise enough to run while other work may be in
    flight.
    """
    return frozenset(
        record.pid
        for record in get_process_manager().list_records(label_prefix=AGENT_PROCESS_LABEL_PREFIX)
    )


def _reap_agents_started_since(known_pids: frozenset[int]) -> tuple[int, ...]:
    """Kill the agent process groups an abandoned attempt started.

    Agents are spawned with ``start_new_session=True``, so each one leads
    its own process group and killing the group takes the agent's own
    children with it -- the subagents and tool subprocesses that would
    otherwise keep editing files after the attempt was abandoned.

    Args:
        known_pids: What :func:`_live_agent_pids` returned before the
            attempt started.

    Returns:
        The process group ids that were signalled.
    """
    manager = get_process_manager()
    reaped: list[int] = []
    for record in manager.list_records(label_prefix=AGENT_PROCESS_LABEL_PREFIX):
        if record.pid in known_pids:
            continue
        try:
            manager.cleanup_orphans(record.pgid)
        except Exception:
            logger.opt(exception=True).warning(
                "conflict_resolution: could not reap abandoned agent group {}",
                record.pgid,
            )
            continue
        reaped.append(record.pgid)
    return tuple(reaped)
