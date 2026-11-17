from __future__ import annotations

import json
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

_LIFECYCLE_MAX_RESPONSE_BYTES: int = 1 * 1024 * 1024
if not _LIFECYCLE_MAX_RESPONSE_BYTES > 0:
    raise RuntimeError(
        f"_LIFECYCLE_MAX_RESPONSE_BYTES must be positive (got {_LIFECYCLE_MAX_RESPONSE_BYTES})"
    )


class _HttpResponseAdapter:
    __slots__ = ("_close", "_read")

    def __init__(self, response: object) -> None:
        read = getattr(response, "read", None)
        close = getattr(response, "close", None)
        if not callable(read) or not callable(close):
            raise TypeError("HTTP response must provide read() and close()")
        self._read: Callable[..., bytes] = read
        self._close: Callable[[], object] = close

    def read(self, n: int = -1) -> bytes:
        return self._read(n)

    def close(self) -> None:
        self._close()


def _http_tools_list_names(endpoint: str, *, timeout: float) -> list[str]:
    request_payload: dict[str, object] = {
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": "1",
        "params": {},
    }
    body = json.dumps(request_payload).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    response = _HttpResponseAdapter(urllib.request.urlopen(request, timeout=timeout))
    try:
        response_data = response.read(_LIFECYCLE_MAX_RESPONSE_BYTES)
    finally:
        response.close()
    raw = response_data.decode("utf-8", errors="replace")
    if not raw:
        return []
    data_lines = [line[len("data: ") :] for line in raw.splitlines() if line.startswith("data: ")]
    payload: object
    if not data_lines:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
    else:
        try:
            payload = json.loads(data_lines[0])
        except json.JSONDecodeError:
            return []
    payload_map = payload if isinstance(payload, dict) else None
    result = payload_map.get("result") if payload_map is not None else None
    if not isinstance(result, dict):
        return []
    tools = result.get("tools")
    if not isinstance(tools, list):
        return []
    names: list[str] = []
    for entry in tools:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if isinstance(name, str):
            names.append(name)
    return names


http_tools_list_names = _http_tools_list_names


__all__ = [
    "_LIFECYCLE_MAX_RESPONSE_BYTES",
    "http_tools_list_names",
]
