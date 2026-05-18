"""Outgoing JSON-RPC response dataclass."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class JsonRpcResponse:
    """Outgoing JSON-RPC response built by McpServer request handlers."""

    jsonrpc: str
    result: object = None
    error: dict[str, object] | None = None
    msg_id: object = None
