
from __future__ import annotations

from enum import StrEnum


class ServerState(StrEnum):

    UNINITIALIZED = "uninitialized"
    RUNNING = "running"
    SHUTDOWN = "shutdown"
