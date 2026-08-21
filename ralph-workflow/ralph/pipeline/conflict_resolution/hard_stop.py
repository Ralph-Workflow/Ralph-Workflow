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
come back.

WHAT ABANDONING MUST CLEAN UP. The caller's next move after a failed
resolution is ``git rebase --abort``, so anything still running that can
write to the worktree is a corruption risk, and the agent process is not
the whole of it. The MCP server the session runs on spawns the tool
subprocesses the agent asked for -- a formatter, a codemod, a test run
-- under their own labels, and those are what actually rewrite files.
All three label families are reaped, and only for processes that were
NOT already running when the attempt started.

WHAT IT CANNOT CLEAN UP. An attempt abandoned before it reaches its
spawn can still spawn afterwards, because Python cannot interrupt the
worker thread. A second sweep after a bounded settle window closes the
common case (a spawn already in flight); an attempt wedged for minutes
before spawning is not closed by anything here, and the reap is
deliberately re-runnable so a caller that needs a later guarantee can
ask for one.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from typing import Protocol

from loguru import logger

from ralph.process.manager import get_process_manager
from ralph.process.teardown import teardown_subtree

__all__ = [
    "HardStop",
    "ProcessRegistry",
    "ResolutionAbandonedError",
    "call_with_hard_stop",
    "live_agent_pids",
    "reap_agents_started_since",
]


class ResolutionAbandonedError(Exception):
    """An attempt outlived its share and was given up on.

    Distinct from a failed round: a failure says this agent could not
    resolve the conflict, and the next candidate may. This says the
    layer BELOW the driver did not return, which the next candidate
    would only prove again -- at the cost of another share of the
    deadline and another thread the interpreter cannot reclaim.
    """


#: Runs one resolution attempt under a wall-clock stop:
#: ``(attempt, timeout_seconds) -> ok | None``, where ``None`` means the
#: attempt was abandoned because it outlived its share. Injected so the
#: driver's behaviour on a wedged agent can be proven without wedging one.
type HardStop = Callable[[Callable[[], bool], float], bool | None]

#: Label prefixes an abandoned attempt owns. ``invoke:`` is the agent
#: itself (``ralph.agents.invoke._process_reader`` / ``._pty_runner``);
#: ``mcp-exec:`` is a tool subprocess the session's MCP server started on
#: the agent's behalf (``ralph.mcp.tools.exec``).
_ATTEMPT_LABEL_PREFIXES = ("invoke:", "mcp-exec:")

#: The session's own MCP server, labelled ``mcp-server`` or
#: ``phase:<phase>:mcp-server`` by ``ralph.mcp.server.lifecycle``.
_MCP_SERVER_LABEL_SUFFIX = "mcp-server"

#: How long an abandoned attempt is given to finish a spawn that was
#: already in flight, before the second reap sweep. Bounded and small:
#: it is added to the attempt's share, not to the pipeline deadline, and
#: a spawn already in flight lands in milliseconds.
_SPAWN_SETTLE_SECONDS = 0.25

#: Names the worker so an abandoned attempt is identifiable in a stack
#: dump taken from a run that survived one.
_ATTEMPT_THREAD_NAME = "ralph-conflict-resolution-attempt"


class _AttemptProcessRecord(Protocol):
    """The fields a reap reads off a tracked process."""

    @property
    def pid(self) -> int:
        """OS process id."""

    @property
    def label(self) -> str | None:
        """Label the process was spawned under, when it has one."""


class ProcessRegistry(Protocol):
    """The process-manager surface a reap depends on."""

    def list_active(self) -> Sequence[_AttemptProcessRecord]:
        """Return the records for processes that have not yet terminated."""


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
    live_before = live_agent_pids()
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
    if not worker.is_alive():
        return outcome[0] if outcome else False

    reaped = reap_agents_started_since(live_before)
    # An attempt abandoned mid-spawn has a process the first sweep could
    # not see yet. The settle window is bounded and is spent only on an
    # attempt that has already been given up on.
    worker.join(_SPAWN_SETTLE_SECONDS)
    reaped += reap_agents_started_since(live_before)
    logger.error(
        "conflict_resolution: the resolution attempt did not return within "
        "{}s and has been abandoned; reaped process group(s): {}",
        round(timeout_seconds, 1),
        list(reaped) or "none",
    )
    return None


def live_agent_pids(*, manager: ProcessRegistry | None = None) -> frozenset[int]:
    """PIDs of the attempt-owned processes running before an attempt.

    Anything under an attempt-owned label that is NOT in this set when an
    attempt is abandoned was started BY that attempt, which is what makes
    the reap precise enough to run while other work may be in flight.

    Args:
        manager: Process registry to read; defaults to the process
            manager singleton.

    Returns:
        The live pids, or an empty set if the registry could not be read
        -- a snapshot that cannot be taken must not fail the attempt it
        was taken for.
    """
    return frozenset(record.pid for record in _attempt_owned_records(manager))


def reap_agents_started_since(
    known_pids: frozenset[int],
    *,
    manager: ProcessRegistry | None = None,
    teardown: Callable[[int], None] | None = None,
) -> tuple[int, ...]:
    """Kill the processes an abandoned attempt started.

    Never raises. This runs on the abandon path, where an exception
    would lose the abandonment itself: the driver would neither record
    the verdict nor return, and would go back to waiting on the layer it
    had just given up on. A registry that cannot be read, or a process
    that will not die, costs the processes it covers -- not the others,
    and not the abandonment.

    Args:
        known_pids: What :func:`live_agent_pids` returned before the
            attempt started.
        manager: Process registry to read; defaults to the singleton.
        teardown: Reaps one process subtree by pid; defaults to
            :func:`~ralph.process.teardown.teardown_subtree`, which
            signals a group only after proving we own it.

    Returns:
        The pids that were reaped.
    """
    reap = teardown if teardown is not None else teardown_subtree
    reaped: list[int] = []
    for record in _attempt_owned_records(manager):
        if record.pid in known_pids:
            continue
        try:
            reap(record.pid)
        except Exception:
            logger.opt(exception=True).warning(
                "conflict_resolution: could not reap abandoned process {}",
                record.pid,
            )
            continue
        reaped.append(record.pid)
    return tuple(reaped)


def _attempt_owned_records(
    manager: ProcessRegistry | None,
) -> tuple[_AttemptProcessRecord, ...]:
    """Live records an attempt owns, or none when the registry cannot be read."""
    registry: ProcessRegistry = manager if manager is not None else get_process_manager()
    try:
        active = tuple(registry.list_active())
    except Exception:
        # ``list_active`` walks a dict other threads spawn into, so it can
        # raise on its own. Reading it is best-effort by construction.
        logger.opt(exception=True).warning(
            "conflict_resolution: could not read the live process set"
        )
        return ()
    return tuple(record for record in active if _is_attempt_owned(record.label))


def _is_attempt_owned(label: str | None) -> bool:
    """Whether a process label belongs to a resolution attempt."""
    if label is None:
        return False
    return label.startswith(_ATTEMPT_LABEL_PREFIXES) or label.endswith(_MCP_SERVER_LABEL_SUFFIX)
