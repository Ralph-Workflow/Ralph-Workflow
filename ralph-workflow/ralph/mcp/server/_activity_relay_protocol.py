from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import socket

from ralph.mcp.server._activity_relay_error import ActivityRelayError

RELAY_MAX_MESSAGE_BYTES = 4096


def receive_bounded_line(connection: socket.socket) -> bytes:
    chunks = bytearray()
    while len(chunks) <= RELAY_MAX_MESSAGE_BYTES:
        piece = connection.recv(min(1024, RELAY_MAX_MESSAGE_BYTES + 1 - len(chunks)))
        if not piece:
            break
        chunks.extend(piece)
        if b"\n" in piece:
            break
    if not chunks or len(chunks) > RELAY_MAX_MESSAGE_BYTES:
        raise ActivityRelayError("missing or oversized relay message")
    line, separator, remainder = bytes(chunks).partition(b"\n")
    if not separator or remainder:
        raise ActivityRelayError("malformed relay framing")
    return line


def decode_json_object(raw: bytes, *, label: str) -> dict[str, object]:
    try:
        decoded: object = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed {label} JSON") from exc
    if not isinstance(decoded, dict):
        raise TypeError(f"{label} must be an object")
    payload: dict[str, object] = {}
    for key, item in decoded.items():
        if not isinstance(key, str):
            raise TypeError(f"{label} keys must be strings")
        value: object = item
        payload[key] = value
    return payload


__all__ = ["RELAY_MAX_MESSAGE_BYTES", "decode_json_object", "receive_bounded_line"]
