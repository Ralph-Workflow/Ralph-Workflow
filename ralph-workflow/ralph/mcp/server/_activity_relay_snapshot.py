"""Immutable diagnostic snapshot for standalone MCP activity relays."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActivityRelaySnapshot:
    """Bounded relay health and delivery evidence for diagnostics."""

    running: bool
    receiver_error: str | None
    sender_error: str | None
    delivered_events: int


__all__ = ["ActivityRelaySnapshot"]
