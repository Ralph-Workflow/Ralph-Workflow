"""Default psutil-based process monitor implementation."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import psutil

from ralph.timeout_defaults import SUBAGENT_OUTPUT_POLL_INTERVAL_SECONDS

from ._process_monitor import ProcessMonitor, ProcessRole
from ._role_classifier import _conservative_role_classifier

if TYPE_CHECKING:
    from collections.abc import Callable

    from ._discovery_strategy import DiscoveryStrategy
    from ._subagent_output_capture import SubagentOutputCapture
    from ._subagent_pid_source import SubagentPidSource



@dataclass(frozen=True)
class _ClassifiedProcess:
    """Concrete implementation of ClassifiedProcess."""

    pid: int
    role: ProcessRole
    cmdline: list[str] | None


class DefaultProcessMonitor(ProcessMonitor):
    """Process monitor that uses psutil to scan the host process tree.

    Classification is intentionally conservative: without an injected
    ``role_classifier`` that is grounded in documented agent behavior, every
    descendant of the host is treated as ``INCIDENTAL_HELPER``. Processes
    outside the host tree are ignored.

    The monitor is agent-agnostic in structure but accepts an optional
    ``role_classifier`` predicate so transport-specific, documentation-grounded
    classification can be injected without editing the watchdog or the monitor.

    Args:
        host_pid: PID of the top-level agent process Ralph launched.
        role_classifier: Optional callable ``(pid, cmdline) -> ProcessRole``.
            When omitted, a built-in conservative classifier is used that never
            promotes a descendant to ``SPAWNED_SUBAGENT``.
        discovery_strategy: Optional ``DiscoveryStrategy`` used to locate
            observable subagent output streams. When omitted, the channel is
            unavailable and ``discover_subagent_outputs`` returns an empty map.
        subagent_pid_source: Optional ``SubagentPidSource`` that returns the
            set of PIDs known to be spawned subagents. When a descendant PID
            is in this set, it is classified as ``SPAWNED_SUBAGENT`` before
            the ``role_classifier`` is consulted. This lets transports such as
            OpenCode identify subagents from first-party stdout evidence
            (the ``ChildLivenessRegistry``) rather than command-line guessing.
        now: Callable returning the current monotonic time.
        poll_interval_seconds: Minimum seconds between process-tree rescans.
    """

    def __init__(
        self,
        host_pid: int,
        *,
        role_classifier: Callable[[int, list[str] | None], ProcessRole] | None = None,
        discovery_strategy: DiscoveryStrategy | None = None,
        subagent_pid_source: SubagentPidSource | None = None,
        now: Callable[[], float] | None = None,
        poll_interval_seconds: float = SUBAGENT_OUTPUT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._host_pid = host_pid
        self._role_classifier = role_classifier or _conservative_role_classifier
        self._discovery_strategy = discovery_strategy
        self._subagent_pid_source = subagent_pid_source
        self._now = now or time.monotonic
        self._poll_interval_seconds = poll_interval_seconds
        self._last_refresh_at: float = 0.0
        self._processes: tuple[_ClassifiedProcess, ...] = ()

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
            host_cmdline = host.cmdline()
        except psutil.Error:
            host_cmdline = None
        classified.append(
            _ClassifiedProcess(
                pid=self._host_pid,
                role=ProcessRole.HOST,
                cmdline=host_cmdline,
            )
        )

        try:
            descendants = host.children(recursive=True)
        except psutil.Error:
            descendants = []

        known_subagent_pids: set[int] = set()
        if self._subagent_pid_source is not None:
            try:
                known_subagent_pids = self._subagent_pid_source.known_subagent_pids()
            except Exception:
                # Discovery source failure must not crash the monitor; fall
                # back to role-classifier-only classification.
                known_subagent_pids = set()

        for proc in descendants:
            try:
                pid = proc.pid
                cmdline = proc.cmdline()
                if pid in known_subagent_pids:
                    role = ProcessRole.SPAWNED_SUBAGENT
                else:
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

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        """Delegate to the injected discovery strategy, if any.

        Returns an empty mapping when no strategy is injected so the
        watchdog degrades gracefully to stdout, MCP, and workspace channels.
        """
        if self._discovery_strategy is None:
            return {}
        try:
            return self._discovery_strategy.discover_subagent_outputs(self._host_pid)
        except Exception:
            return {}
