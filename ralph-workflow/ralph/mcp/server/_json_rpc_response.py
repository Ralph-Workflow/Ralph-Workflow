from __future__ import annotations

from dataclasses import dataclass


@dataclass
class JsonRpcResponse:
    jsonrpc: str
    result: object = None
    error: dict[str, object] | None = None
    msg_id: object = None
