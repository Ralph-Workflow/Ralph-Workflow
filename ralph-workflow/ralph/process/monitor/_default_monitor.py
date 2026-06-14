"""Default psutil-based process monitor implementation."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import psutil

from ralph.timeout_defaults import SUBAGENT_OUTPUT_POLL_INTERVAL_SECONDS

from ._process_monitor import ProcessMonitor, ProcessRole

if TYPE_CHECKING:
    from collections.abc import Callable



@dataclass(frozen=True)
class _ClassifiedProcess:
    """Concrete implementation of ClassifiedProcess."""

    pid: int
    role: ProcessRole
    cmdline: list[str] | None


class DefaultProcessMonitor(ProcessMonitor):
    """Process monitor that uses psutil to scan the host process tree.

    Classification is intentionally conservative: only processes whose command
    line matches a known subagent pattern are classified as
    ``SPAWNED_SUBAGENT``. Everything else that is a descendant of the host is
    treated as ``INCIDENTAL_HELPER``; processes outside the host tree are
    ignored.

    The monitor is agent-agnostic in structure but accepts an optional
    ``role_classifier`` predicate so agent-specific CLI patterns can be
    injected without editing the watchdog.

    Args:
        host_pid: PID of the top-level agent process Ralph launched.
        role_classifier: Optional callable ``(pid, cmdline) -> ProcessRole``.
            When omitted, a built-in classifier is used that recognises common
            subagent CLI tokens (e.g. ``worker``, ``subagent``, ``task``).
        now: Callable returning the current monotonic time.
        poll_interval_seconds: Minimum seconds between process-tree rescans.
    """

    _SUBAGENT_TOKENS: frozenset[str] = frozenset(
        {"worker", "subagent", "task", "agent", "claude", "opencode"}
    )

    def __init__(
        self,
        host_pid: int,
        *,
        role_classifier: Callable[[int, list[str] | None], ProcessRole] | None = None,
        now: Callable[[], float] | None = None,
        poll_interval_seconds: float = SUBAGENT_OUTPUT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._host_pid = host_pid
        self._role_classifier = role_classifier or self._default_classifier
        self._now = now or time.monotonic
        self._poll_interval_seconds = poll_interval_seconds
        self._last_refresh_at: float = 0.0
        self._processes: tuple[_ClassifiedProcess, ...] = ()

    def _default_classifier(self, _pid: int, cmdline: list[str] | None) -> ProcessRole:
        """Classify a descendant process based on its command line."""
        if not cmdline:
            return ProcessRole.INCIDENTAL_HELPER
        lowered = " ".join(cmdline).lower()
        if any(token in lowered for token in self._SUBAGENT_TOKENS):
            return ProcessRole.SPAWNED_SUBAGENT
        return ProcessRole.INCIDENTAL_HELPER

    def refresh(self) -> None:
        """Rescan the process tree if the poll interval has elapsed."""
        now = self._now()
        if now - self._last_refresh_at < self._poll_interval_seconds:
            return
        self._last_refresh_at = now
        try:
            host = psutil.Process(self._host_pid)
        except psutil.Error:
            self._processes = ()
            return

        classified: list[_ClassifiedProcess] = []
        try:
            descendants = host.children(recursive=True)
        except psutil.Error:
            descendants = []

        for proc in descendants:
            try:
                pid = proc.pid
                cmdline = proc.cmdline()
                role = self._role_classifier(pid, cmdline)
                classified.append(_ClassifiedProcess(pid=pid, role=role, cmdline=cmdline))
            except (psutil.Error, OSError):
                # Process vanished between listing and inspection; skip it.
                continue

        self._processes = tuple(classified)

    def live_subagent_count(self) -> int:
        """Return the number of live spawned subagents."""
        self.refresh()
        return sum(1 for p in self._processes if p.role == ProcessRole.SPAWNED_SUBAGENT)

    def classified_processes(self) -> tuple[_ClassifiedProcess, ...]:
        """Return all classified descendant processes."""
        self.refresh()
        return self._processes
