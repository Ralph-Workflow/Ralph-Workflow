"""Connectivity event dataclass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from ralph.recovery.connectivity_state import ConnectivityState


@dataclass
class ConnectivityEvent:
    """A snapshot of a connectivity state transition."""

    state: ConnectivityState
    since: datetime
    reason: str
