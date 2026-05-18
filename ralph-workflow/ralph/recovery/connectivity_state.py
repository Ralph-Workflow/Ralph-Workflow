"""Connectivity state enumeration."""

from __future__ import annotations

from enum import StrEnum


class ConnectivityState(StrEnum):
    """Enumeration of observed network connectivity states."""

    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"
