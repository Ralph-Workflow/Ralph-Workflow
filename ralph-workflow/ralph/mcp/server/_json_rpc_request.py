from __future__ import annotations

from dataclasses import dataclass


@dataclass
class JsonRpcRequest:
    jsonrpc: str
    method: str
    params: dict[str, object] | None = None
    msg_id: object = None
