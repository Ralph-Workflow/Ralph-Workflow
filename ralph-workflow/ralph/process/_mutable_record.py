"""Mutable liveness state for a tracked child process.

This dataclass is updated in place by the process manager and parallel
worker coordinator as heartbeats, progress, and acknowledgements arrive.
It is intentionally separate from the immutable ``ProcessRecord`` so the
manager can mutate cheap liveness fields without rewriting history.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MutableRecord:
    """Runtime-mutable liveness metadata for one child process.

    ``child_id`` and ``scope_prefix`` identify the child. ``pid`` is the
    OS process id once known. The ``last_*`` fields are updated by the
    coordinator from heartbeat/progress/ack traffic. ``terminal_state``
    and ``lease_expires_at`` support graceful shutdown and lease-based
    recovery.
    """

    child_id: str
    scope_prefix: str
    pid: int | None
    started_at: float
    last_progress_at: float | None = None
    last_heartbeat_at: float | None = None
    last_ack_at: float | None = None
    last_known_phase: str = "spawned"
    terminal_state: str | None = None
    lease_expires_at: float | None = None


__all__ = ["MutableRecord"]
