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
-- and those are what actually rewrite files. Two label families are
reaped, the agent and that server, and only for processes that were NOT
already running when the attempt started. The tool subprocesses are not
reaped BY LABEL at all: they are registered in the server's own process
manager, not this one, and are reached as live children of the server
whose subtree is torn down.

WHAT IT CANNOT CLEAN UP. An attempt abandoned before it reaches its
spawn can still spawn afterwards, because Python cannot interrupt the
worker thread. A second sweep after a bounded settle window closes the
common case (a spawn already in flight); an attempt wedged for minutes
before spawning is not closed by anything here, and the reap is
deliberately re-runnable so a caller that needs a later guarantee can
ask for one.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Sequence
from typing import Protocol

from loguru import logger

from ralph.pipeline.conflict_resolution._resolution_abandoned_error import (
    ResolutionAbandonedError,
)
from ralph.process.manager import get_process_manager
from ralph.process.teardown import teardown_subtree

__all__ = [
    "REAP_WAIT_SECONDS",
    "SPAWN_SETTLE_SECONDS",
    "HardStop",
    "ProcessRegistry",
    "ResolutionAbandonedError",
    "call_with_hard_stop",
    "live_agent_pids",
    "reap_agents_started_since",
]


#: Runs one resolution attempt under a wall-clock stop:
#: ``(attempt, timeout_seconds) -> ok | None``, where ``None`` means the
#: attempt was abandoned because it outlived its share. Injected so the
#: driver's behaviour on a wedged agent can be proven without wedging one.
type HardStop = Callable[[Callable[[], bool], float], bool | None]

#: The agent process itself (``ralph.agents.invoke._process_reader`` /
#: ``._pty_runner``). The tool subprocesses that actually rewrite files
#: are NOT here: ``ralph.mcp.tools.exec`` spawns them into the MCP
#: SERVER's process registry, not this process's, so they are reached by
#: reaping the server's subtree rather than by label.
_ATTEMPT_LABEL_PREFIXES = ("invoke:",)

#: The session's own MCP server, labelled ``mcp-server`` or
#: ``phase:<phase>:mcp-server`` by ``ralph.mcp.server.lifecycle``. Its
#: subtree is where the agent's tool subprocesses live.
_MCP_SERVER_LABEL_SUFFIX = "mcp-server"

#: How long an abandoned attempt is given to finish a spawn that was
#: already in flight, before the second reap sweep. Bounded and small:
#: it is added to the attempt's share, not to the pipeline deadline, and
#: a spawn already in flight lands in milliseconds.
SPAWN_SETTLE_SECONDS = 0.25

#: Names the worker so an abandoned attempt is identifiable in a stack
#: dump taken from a run that survived one.
_ATTEMPT_THREAD_NAME = "ralph-conflict-resolution-attempt"

#: Names the thread an abandoned attempt is cleaned up and announced on.
_ABANDON_CLEANUP_THREAD_NAME = "ralph-conflict-resolution-abandoned"

#: How long the caller waits for that cleanup before returning without
#: it. The kill should normally land before the caller aborts the
#: rebase; a teardown that will not finish may not hold the driver.
REAP_WAIT_SECONDS = 5.0


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
    *,
    manager: ProcessRegistry | None = None,
    teardown: Callable[[int], None] | None = None,
    report: Callable[[float, tuple[int, ...]], None] | None = None,
    reap_wait_seconds: float = REAP_WAIT_SECONDS,
) -> bool | None:
    """Run ``attempt``, abandoning it if it outlives ``timeout_seconds``.

    Args:
        attempt: The resolution attempt to run. Its exceptions are
            absorbed: a round that died is a failed round, never a
            failed run.
        timeout_seconds: How long the caller is willing to wait.
        manager: Process registry the reap reads; defaults to the
            process manager singleton.
        teardown: Reaps one process subtree by pid; defaults to
            :func:`~ralph.process.teardown.teardown_subtree`.
        report: Announces an abandonment; defaults to a log line. Always
            called on the cleanup thread, never this one.
        reap_wait_seconds: How long the caller will wait for the reap to
            finish before returning without it.

    Returns:
        What ``attempt`` returned, ``False`` if it raised, or ``None``
        if it had not returned when the stop expired. ``None`` is the
        only outcome the caller must treat as "the layer below is
        wedged" rather than "this agent could not resolve it".
    """
    live_before = live_agent_pids(manager=manager)
    outcome: list[bool] = []
    # The attempt's OWN completion, not the worker's liveness. A thread
    # that has returned its value is not yet a thread that has died, and
    # deciding on ``is_alive`` abandons -- and reaps -- resolutions that
    # actually succeeded, right on the boundary where the stop is most
    # likely to land.
    finished = threading.Event()

    def _run_attempt() -> None:
        try:
            outcome.append(attempt())
        except Exception:
            logger.opt(exception=True).warning(
                "conflict_resolution: the resolution attempt raised; failing the round"
            )
        finally:
            finished.set()

    worker = threading.Thread(target=_run_attempt, name=_ATTEMPT_THREAD_NAME, daemon=True)
    worker.start()
    if finished.wait(timeout_seconds):
        return outcome[0] if outcome else False

    _clean_up_after_abandoning(
        live_before,
        finished,
        timeout_seconds,
        manager=manager,
        teardown=teardown,
        report=report,
        reap_wait_seconds=reap_wait_seconds,
    )
    return None


def _clean_up_after_abandoning(
    live_before: frozenset[int] | None,
    finished: threading.Event,
    timeout_seconds: float,
    *,
    manager: ProcessRegistry | None,
    teardown: Callable[[int], None] | None,
    report: Callable[[float, tuple[int, ...]], None] | None,
    reap_wait_seconds: float,
) -> None:
    """Reap and announce on a thread of their own, waited on for a bounded time.

    NONE of this may run on the caller's thread. Two reasons, both of
    them the reason the caller is here at all:

    * Every diagnostic in the reap goes through loguru, whose sink
      prints via the same ``rich.Console`` the status bar paints with
      (``ralph.display.log_sink``), under a per-handler lock and the
      Console's own. A worker wedged inside a display write holds
      exactly that, so saying "I gave up on that thread" on this thread
      hands the freeze straight back -- and the conditions that produce
      those lines (a registry that raises, a teardown that fails) are
      the ones the abandon path exists for.
    * ``teardown_subtree`` escalates SIGTERM to SIGKILL with waits in
      between: seconds per process, serially, over two sweeps, all of it
      after the deadline has already expired.

    The caller still waits ``reap_wait_seconds`` for it, because the
    next thing it does is abort a rebase and the kill should normally
    land first. It waits on an Event, which no wedged thread can hold.
    """
    done = threading.Event()

    def _clean_up() -> None:
        reaped: tuple[int, ...] = ()
        try:
            reaped = reap_agents_started_since(live_before, manager=manager, teardown=teardown)
            # An attempt abandoned mid-spawn has a process the first
            # sweep could not see yet. What the first sweep took is
            # added to what it already knew about, so the second sweep
            # reaps only what is genuinely new.
            finished.wait(SPAWN_SETTLE_SECONDS)
            already_seen = None if live_before is None else live_before | frozenset(reaped)
            reaped += reap_agents_started_since(already_seen, manager=manager, teardown=teardown)
        finally:
            done.set()
        announce = report if report is not None else _log_abandonment
        with contextlib.suppress(Exception):
            announce(timeout_seconds, reaped)

    try:
        threading.Thread(target=_clean_up, name=_ABANDON_CLEANUP_THREAD_NAME, daemon=True).start()
    except RuntimeError:
        # Out of threads. The abandonment still stands -- losing it here
        # would send the caller back to the layer it just gave up on --
        # so the cleanup is simply skipped, and said so.
        logger.opt(exception=True).error(
            "conflict_resolution: could not start the cleanup thread for an "
            "abandoned attempt; its processes were NOT reaped"
        )
        return
    done.wait(reap_wait_seconds)


def _log_abandonment(timeout_seconds: float, reaped: tuple[int, ...]) -> None:
    """Default announcement: one operator-facing log line."""
    logger.error(
        "conflict_resolution: the resolution attempt did not return within "
        "{}s and has been abandoned; reaped process group(s): {}",
        round(timeout_seconds, 1),
        list(reaped) or "none",
    )


def live_agent_pids(*, manager: ProcessRegistry | None = None) -> frozenset[int] | None:
    """PIDs of the attempt-owned processes running before an attempt.

    Anything under an attempt-owned label that is NOT in this set when an
    attempt is abandoned was started BY that attempt, which is what makes
    the reap precise enough to run while other work may be in flight.

    Args:
        manager: Process registry to read; defaults to the process
            manager singleton.

    Returns:
        The live pids, or ``None`` when the registry could not be read.
        ``None`` is not an empty set: an empty set says nothing was
        running, which would make every live agent and MCP server in
        this process -- the parent run's included -- look like something
        this attempt started.
    """
    records = _attempt_owned_records(manager)
    if records is None:
        return None
    return frozenset(record.pid for record in records)


def reap_agents_started_since(
    known_pids: frozenset[int] | None,
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
            attempt started. ``None`` means the snapshot could not be
            taken, and nothing is reaped: without it there is no way to
            tell this attempt's processes from everyone else's.
        manager: Process registry to read; defaults to the singleton.
        teardown: Reaps one process subtree by pid; defaults to
            :func:`~ralph.process.teardown.teardown_subtree`, which
            signals a group only after proving we own it.

    Returns:
        The pids that were reaped.
    """
    if known_pids is None:
        logger.warning(
            "conflict_resolution: no process snapshot for the abandoned attempt; "
            "reaping nothing rather than guessing what it started"
        )
        return ()
    try:
        return _reap_unknown_records(known_pids, manager, teardown)
    except Exception:
        # Whatever went wrong, it may not cost the caller the
        # abandonment itself.
        logger.opt(exception=True).warning("conflict_resolution: the reap failed")
        return ()


def _reap_unknown_records(
    known_pids: frozenset[int],
    manager: ProcessRegistry | None,
    teardown: Callable[[int], None] | None,
) -> tuple[int, ...]:
    """Reap every attempt-owned record that is not in ``known_pids``."""
    reap = teardown if teardown is not None else teardown_subtree
    reaped: list[int] = []
    for record in _attempt_owned_records(manager) or ():
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
) -> tuple[_AttemptProcessRecord, ...] | None:
    """Live records an attempt owns, or ``None`` when they cannot be read."""
    try:
        registry: ProcessRegistry = manager if manager is not None else get_process_manager()
        active = tuple(registry.list_active())
        return tuple(record for record in active if _is_attempt_owned(record.label))
    except Exception:
        # ``list_active`` walks a dict other threads spawn into, so it can
        # raise on its own. Reading it is best-effort by construction --
        # but "could not read" is never "nothing was there".
        logger.opt(exception=True).warning(
            "conflict_resolution: could not read the live process set"
        )
        return None


def _is_attempt_owned(label: str | None) -> bool:
    """Whether a process label belongs to a resolution attempt."""
    if label is None:
        return False
    return label.startswith(_ATTEMPT_LABEL_PREFIXES) or label.endswith(_MCP_SERVER_LABEL_SUFFIX)
