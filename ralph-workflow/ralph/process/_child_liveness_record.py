"""ChildLivenessRecord — immutable snapshot of a single child's liveness state."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChildLivenessRecord:
    """Immutable snapshot of a single child's liveness state."""

    child_id: str
    scope_prefix: str
    pid: int | None
    started_at: float
    last_progress_at: float | None
    last_heartbeat_at: float | None
    last_ack_at: float | None
    last_known_phase: str = "spawned"
    terminal_state: str | None = None
    lease_expires_at: float | None = None


__all__ = ["ChildLivenessRecord"]
