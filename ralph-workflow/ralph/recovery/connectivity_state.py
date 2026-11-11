
from __future__ import annotations

from enum import StrEnum


class ConnectivityState(StrEnum):

    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"
