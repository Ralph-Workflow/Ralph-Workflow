"""Configurable timing bounds for MCP make-before-break handovers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HandoverTiming:
    """Bounded timing policy for one restart-aware bridge.

    Production uses the default values.  The values are injectable so callers
    can run the same handover state machine with a different operational
    timing policy without mutating module globals.
    """

    ack_timeout_s: float = 1.0
    kill_wait_s: float = 1.0
    drain_s: float = 0.5
    unkillable_reap_backoff_s: float = 0.5
    unkillable_reap_retry_s: float = 1.0

    def __post_init__(self) -> None:
        if not self.ack_timeout_s > 0.0:
            raise ValueError("ack_timeout_s must be positive")
        if not self.kill_wait_s > 0.0:
            raise ValueError("kill_wait_s must be positive")
        if not self.drain_s > 0.0:
            raise ValueError("drain_s must be positive")
        if not self.unkillable_reap_backoff_s >= 0.0:
            raise ValueError("unkillable_reap_backoff_s must be non-negative")
        if not self.unkillable_reap_retry_s > 0.0:
            raise ValueError("unkillable_reap_retry_s must be positive")


__all__ = ["HandoverTiming"]
