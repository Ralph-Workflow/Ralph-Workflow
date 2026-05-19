"""Parsed JSON-RPC request dataclass."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class JsonRpcRequest:
    """Parsed representation of an incoming JSON-RPC request."""

    jsonrpc: str
    method: str
    params: dict[str, object] | None = None
    msg_id: object = None
