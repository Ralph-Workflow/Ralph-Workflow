"""Typed relay exceptions and snapshots for standalone MCP supervision."""

from __future__ import annotations

from dataclasses import dataclass


class ActivityRelayError(RuntimeError):
    """A typed failure in the conflict-resolution liveness relay."""


@dataclass(frozen=True)
class ActivityRelaySnapshot:
    """Bounded relay health and delivery evidence for diagnostics."""

    running: bool
    receiver_error: str | None
    sender_error: str | None
    delivered_events: int


__all__ = ["ActivityRelayError", "ActivityRelaySnapshot"]
