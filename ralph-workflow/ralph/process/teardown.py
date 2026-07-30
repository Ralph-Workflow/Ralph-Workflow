"""Process subtree teardown utilities.

Ensures that every subagent spawned by a host process is reaped when a phase,
iteration, or session ends. The teardown walks the entire process tree (all
descendants, transitively) and escalates from SIGTERM to SIGKILL after a short
grace window.

When the host process has already exited, the descendants are reaped by
signaling the host's process group (the host is the session leader because
agents are spawned with ``start_new_session=True``). This closes the race where
a dead host PID can no longer be enumerated with psutil but its children still
exist.

═══ OWNERSHIP GUARD — a PID alone is NEVER authority to kill ═══

A PID is a reusable integer, not a capability. Acting on one that this process
did not spawn is how a reaper turns into a machine-wide kill:

- ``teardown_subtree(1)`` enumerated ``launchd``'s recursive descendants — i.e.
  every process on the host — and SIGTERM'd them all. Fake process managers in
  the test suite hand out ``itertools.count(1)`` PIDs, so ``make verify`` shut
  the developer's Mac down.
- ``os.killpg(dead_pid, SIGKILL)`` on an exited host signals whatever unrelated
  process group has since inherited that number. Every successful
  ``run_process`` call reached this path, because ``communicate()`` reaps the
  child before teardown runs.

So every destructive action below is gated on proof of ownership: the target
must be a live descendant of THIS process (``_is_own_descendant``), and a
process group is only ever signalled when it was verified — while its leader
was still alive — to be a session this process created
(``verified_child_session_pgid``). Unverifiable targets are skipped, never
guessed at. Leaking a stray child is recoverable; SIGKILLing a stranger's
process group is not.
"""

from __future__ import annotations

import contextlib
import os
import signal
import threading
import time
from collections import OrderedDict
from typing import Protocol, runtime_checkable

import psutil

from ralph.timeout_defaults import KILL_ESCALATION_CEILING_MS

# Depth bound for the parent-chain walk that proves ownership. Real spawn
# trees are shallow (host -> worker -> fork); the bound only stops a walk
# through a pathological or cyclic chain.
_MAX_ANCESTOR_WALK: int = 32

# Sessions this process created, recorded at spawn while the leader was still
# alive. Ownership cannot be re-derived once the leader exits — the PID number
# is reusable — so it is proven up front and remembered here.
_SESSION_REGISTRY_MAX: int = 256
_SESSION_REGISTRY: OrderedDict[int, int] = OrderedDict()  # bounded-accumulator-ok: FIFO-evicted at _SESSION_REGISTRY_MAX entries, and each entry is dropped by teardown_subtree as soon as its subtree is reaped.
_SESSION_REGISTRY_LOCK = threading.Lock()


def _own_pgid() -> int | None:
    """Return this process's own process-group ID, or None if unavailable."""
    if not hasattr(os, "getpgid"):
        return None
    try:
        return os.getpgid(0)
    except OSError:
        return None


def _is_own_descendant(proc: psutil.Process) -> bool:
    """Return True when ``proc`` is a descendant of the current process.

    This is the ownership proof for every kill below. A PID we did not spawn
    belongs to someone else — the user's editor, a browser, ``launchd`` — and
    must never be signalled no matter how the number reached us.
    """
    self_pid = os.getpid()
    current = proc
    for _ in range(_MAX_ANCESTOR_WALK):
        try:
            parent = current.parent()
        except psutil.Error:
            return False
        if parent is None or parent.pid <= 1:
            return False
        if parent.pid == self_pid:
            return True
        current = parent
    return False


def _group_is_safe(pgid: int) -> bool:
    """Return True when ``pgid`` is a group we may signal.

    Rejects PID 0/1 (``killpg(0, ...)`` signals our OWN group, taking down the
    caller and its siblings) and our own group.
    """
    if pgid <= 1:
        return False
    own = _own_pgid()
    return own is None or pgid != own


def verified_child_session_pgid(pid: int) -> int | None:
    """Return ``pid`` as a process-group ID iff we own that session.

    Call this while the child is still ALIVE — the answer is what makes a later
    group signal safe once the child is gone. Returns the PGID only when the
    process is a live descendant of this process AND is its own group leader
    (which holds exactly for children spawned with ``start_new_session=True``).
    Returns None otherwise; None means "do not signal any group".
    """
    if pid <= 1 or not hasattr(os, "getpgid"):
        return None
    try:
        proc = psutil.Process(pid)
    except psutil.Error:
        return None
    if not _is_own_descendant(proc):
        return None
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return None
    if pgid != pid or not _group_is_safe(pgid):
        return None
    return pgid


def register_child_session(pid: int) -> int | None:
    """Record ``pid``'s process group as one this process owns.

    Call once, right after spawning a ``start_new_session=True`` child, while
    it is still alive. The recorded group is what lets ``teardown_subtree``
    reap descendants that outlive the leader: after the leader exits, nothing
    about the bare PID can prove ownership any more.

    Returns the verified PGID, or None when ownership could not be proven (a
    fabricated PID from a fake process manager, or a child sharing our group).
    """
    pgid = verified_child_session_pgid(pid)
    if pgid is None:
        return None
    with _SESSION_REGISTRY_LOCK:
        _SESSION_REGISTRY[pid] = pgid
        _SESSION_REGISTRY.move_to_end(pid)
        while len(_SESSION_REGISTRY) > _SESSION_REGISTRY_MAX:
            _SESSION_REGISTRY.popitem(last=False)
    return pgid


def _registered_session_pgid(pid: int) -> int | None:
    """Return the PGID recorded for ``pid`` at spawn, if any."""
    with _SESSION_REGISTRY_LOCK:
        return _SESSION_REGISTRY.get(pid)


def _forget_child_session(pid: int) -> None:
    """Drop ``pid``'s registry entry once its subtree has been reaped."""
    with _SESSION_REGISTRY_LOCK:
        _SESSION_REGISTRY.pop(pid, None)


def _resolve_target(host_pid: int, pgid: int | None) -> tuple[psutil.Process | None, int | None]:
    """Resolve what may legitimately be killed for ``host_pid``.

    Returns ``(host, group)``. ``host`` is non-None only for a live descendant
    of this process; ``group`` is non-None only for a process group this
    process is known to have created. Every refusal path returns ``(None, ...)``
    with no group, so an unverifiable PID results in no signal at all.
    """
    group = pgid if pgid is not None and _group_is_safe(pgid) else None
    if group is None:
        # Fall back to what was proven at spawn time, which survives the
        # leader's exit precisely because it was captured before it.
        registered = _registered_session_pgid(host_pid)
        group = registered if registered is not None and _group_is_safe(registered) else None

    if host_pid <= 1:
        # 0 means "our own group" to killpg and 1 is init/launchd, whose
        # descendant set is the entire machine. Never a legitimate target.
        return None, None

    try:
        host = psutil.Process(host_pid)
    except psutil.Error:
        # The host already exited. A bare PID here may belong to an unrelated
        # process that inherited the number; only a pre-verified group stands.
        return None, group

    if not _is_own_descendant(host):
        # Not ours to kill. Fake and stale PIDs land here.
        return None, None

    # Capture the group while the leader is alive so descendants that
    # re-parent away mid-teardown stay reachable.
    return host, group if group is not None else verified_child_session_pgid(host_pid)


@runtime_checkable
class ProcessTeardown(Protocol):
    """Protocol for reaping a process subtree."""

    def teardown_subtree(self, host_pid: int) -> None:
        """Kill the entire process subtree rooted at ``host_pid``.

        Must reap the host and all descendants, transitively. Implementations
        should escalate from SIGTERM to SIGKILL after a bounded grace window.
        """
        ...


class DefaultProcessTeardown:
    """Reap a process subtree using psutil.

    Sends SIGTERM to the host and all descendants, waits up to
    ``KILL_ESCALATION_CEILING_MS`` for them to exit, then sends SIGKILL to any
    survivors. The implementation gracefully handles processes that disappear
    between enumeration and signal delivery.

    Args:
        kill_escalation_ms: Milliseconds to wait between SIGTERM and SIGKILL.
            Defaults to ``KILL_ESCALATION_CEILING_MS``.
    """

    def __init__(self, kill_escalation_ms: float = KILL_ESCALATION_CEILING_MS) -> None:
        self._kill_escalation_ms = kill_escalation_ms

    def teardown_subtree(self, host_pid: int, *, pgid: int | None = None) -> None:
        """Kill the host process and all of its descendants.

        Args:
            host_pid: PID of the subtree root. Ignored unless it is a live
                descendant of this process — see the module docstring.
            pgid: Process group verified via ``verified_child_session_pgid``
                while the host was alive. Supplying it lets the group still be
                reaped after the host exits; without it, an already-dead host
                is a no-op, because its PID number may since have been reused.
        """
        host, group = _resolve_target(host_pid, pgid)
        _forget_child_session(host_pid)

        if host is None:
            # Nothing we own is reachable by PID. A group survives here only
            # when it was verified while its leader was alive.
            if group is not None:
                self._signal_process_group(group)
            return

        procs: list[psutil.Process] = []
        try:
            procs.append(host)
            procs.extend(host.children(recursive=True))
        except psutil.Error:
            pass

        # First pass: SIGTERM
        for proc in procs:
            with contextlib.suppress(psutil.Error):
                proc.terminate()

        gone = self._await_exit(procs)

        # Second pass: SIGKILL for survivors
        for proc in procs:
            if proc.pid in gone:
                continue
            try:
                if proc.is_running():
                    proc.kill()
            except psutil.Error:
                pass

        # Finally sweep the verified group: a descendant forked after the
        # enumeration above, or one that re-parented away from the host, is
        # still a member of the session we created.
        if group is not None:
            self._signal_process_group(group)

    def _await_exit(self, procs: list[psutil.Process]) -> set[int]:
        """Poll until every process in ``procs`` is gone or the grace expires.

        Returns the PIDs observed to have exited.
        """
        deadline = time.monotonic() + (self._kill_escalation_ms / 1000.0)
        gone: set[int] = set()
        # Adaptive poll interval: starts at 10ms so a small process tree
        # tears down quickly, lengthens to 25ms after the first sleep so
        # large trees still finish inside the test budget.
        poll_interval = 0.01
        while time.monotonic() < deadline:
            for proc in procs:
                if proc.pid in gone:
                    continue
                try:
                    if not proc.is_running():
                        gone.add(proc.pid)
                except psutil.Error:
                    gone.add(proc.pid)
            if len(gone) >= len(procs):
                break
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.5, 0.025)
        return gone

    def _signal_process_group(self, pgid: int) -> None:
        """Escalate from SIGTERM to SIGKILL for every process in ``pgid``.

        Callers MUST pass a group verified through
        ``verified_child_session_pgid``; the guard below is a backstop, not the
        ownership check. The call is best-effort and silently ignores missing
        or empty groups.
        """
        if not _group_is_safe(pgid) or not hasattr(os, "killpg"):
            return

        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(pgid, signal.SIGTERM)

        deadline = time.monotonic() + (self._kill_escalation_ms / 1000.0)
        while time.monotonic() < deadline:
            try:
                # When the group no longer exists, the kernel raises
                # ProcessLookupError and we are done.
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return
            except (PermissionError, OSError):
                return
            time.sleep(0.05)

        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(pgid, signal.SIGKILL)


def teardown_subtree(
    host_pid: int,
    *,
    kill_escalation_ms: float = KILL_ESCALATION_CEILING_MS,
    pgid: int | None = None,
) -> None:
    """Convenience function that reaps a subtree with the default implementation."""
    DefaultProcessTeardown(kill_escalation_ms=kill_escalation_ms).teardown_subtree(
        host_pid, pgid=pgid
    )


__all__ = [
    "DefaultProcessTeardown",
    "ProcessTeardown",
    "register_child_session",
    "teardown_subtree",
    "verified_child_session_pgid",
]
