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
"""

from __future__ import annotations

import contextlib
import os
import signal
import time
from typing import Protocol, runtime_checkable

import psutil

from ralph.timeout_defaults import KILL_ESCALATION_CEILING_MS


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

    def teardown_subtree(self, host_pid: int) -> None:
        """Kill the host process and all of its descendants."""
        try:
            host = psutil.Process(host_pid)
        except psutil.Error:
            # The host already exited. Because agents are spawned with
            # ``start_new_session=True``, the host PID is also the process
            # group ID. Signal the group so any descendants that outlived
            # the session leader are reaped.
            self._signal_process_group(host_pid)
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

        deadline = time.monotonic() + (self._kill_escalation_ms / 1000.0)
        gone: set[int] = set()
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
                return
            time.sleep(0.05)

        # Second pass: SIGKILL for survivors
        for proc in procs:
            if proc.pid in gone:
                continue
            try:
                if proc.is_running():
                    proc.kill()
            except psutil.Error:
                pass

    def _signal_process_group(self, pgid: int) -> None:
        """Escalate from SIGTERM to SIGKILL for every process in ``pgid``.

        The call is best-effort and silently ignores missing or empty groups.
        """
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
) -> None:
    """Convenience function that reaps a subtree with the default implementation."""
    DefaultProcessTeardown(kill_escalation_ms=kill_escalation_ms).teardown_subtree(host_pid)


__all__ = [
    "DefaultProcessTeardown",
    "ProcessTeardown",
    "teardown_subtree",
]
