from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActivityRelaySnapshot:
    running: bool
    receiver_error: str | None
    sender_error: str | None
    delivered_events: int


__all__ = ["ActivityRelaySnapshot"]
