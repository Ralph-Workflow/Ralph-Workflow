from __future__ import annotations

import json
import urllib.request
from typing import IO, cast

_LIFECYCLE_MAX_RESPONSE_BYTES: int = 1 * 1024 * 1024
if not _LIFECYCLE_MAX_RESPONSE_BYTES > 0:
    raise RuntimeError(
        f"_LIFECYCLE_MAX_RESPONSE_BYTES must be positive (got {_LIFECYCLE_MAX_RESPONSE_BYTES})"
    )


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
    response = cast("IO[bytes]", urllib.request.urlopen(request, timeout=timeout))
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
            payload = cast("object", json.loads(raw))
        except json.JSONDecodeError:
            return []
    else:
        try:
            payload = cast("object", json.loads(data_lines[0]))
        except json.JSONDecodeError:
            return []
    payload_map = payload if isinstance(payload, dict) else None
    result = payload_map.get("result") if payload_map is not None else None
    if not isinstance(result, dict):
        return []
    result_map = cast("dict[str, object]", result)
    tools = result_map.get("tools")
    if not isinstance(tools, list):
        return []
    return [
        cast("str", entry_map["name"])
        for entry in tools
        for entry_map in [cast("dict[str, object]", entry)]
        if isinstance(entry, dict) and isinstance(entry_map.get("name"), str)
    ]


http_tools_list_names = _http_tools_list_names


__all__ = [
    "_LIFECYCLE_MAX_RESPONSE_BYTES",
    "http_tools_list_names",
]
