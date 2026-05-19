"""MutableRecord — mutable liveness state for a tracked child."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MutableRecord:
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
