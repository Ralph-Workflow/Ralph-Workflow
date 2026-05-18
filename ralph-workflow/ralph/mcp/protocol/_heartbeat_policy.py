"""Heartbeat policy dataclass for MCP health monitoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import timedelta


@dataclass(frozen=True)
class HeartbeatPolicy:
    """Supervision interval configuration for active MCP health monitoring."""

    interval: timedelta
