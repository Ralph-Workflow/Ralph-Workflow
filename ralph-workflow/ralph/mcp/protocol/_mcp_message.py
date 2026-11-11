
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MCPMessage:

    method: str
    params: dict[str, object] | None = None
    msg_id: str | int | None = None
    error: dict[str, object] | None = None
