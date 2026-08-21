"""Reading a JSON-RPC ``tools/list`` response from a running MCP server.

Split out of :mod:`ralph.mcp.server.lifecycle` with the byte cap that
bounds it: the read and the reason the read is bounded belong together.
"""

from __future__ import annotations

import json
import urllib.request
from typing import IO, cast

#: Cap for a single ``tools/list`` read. The parent bounds the read so a
#: misbehaving upstream that streams an unbounded response body cannot
#: OOM it (AC-08). 1 MiB is well above any realistic ``tools/list`` JSON
#: payload and matches the size budget an operator would expect for a
#: discardable wrap-up response.
_LIFECYCLE_MAX_RESPONSE_BYTES: int = 1 * 1024 * 1024
if not _LIFECYCLE_MAX_RESPONSE_BYTES > 0:
    raise RuntimeError(
        f"_LIFECYCLE_MAX_RESPONSE_BYTES must be positive (got {_LIFECYCLE_MAX_RESPONSE_BYTES})"
    )


def _http_tools_list_names(endpoint: str, *, timeout: float) -> list[str]:
    """Send a JSON-RPC ``tools/list`` request and return the tool names.

    Returns an empty list on transport errors (the caller is
    responsible for diagnosing the underlying cause). Used by
    :meth:`RestartAwareMcpBridge._verify_alias_present` after a
    respawn to confirm the post-respawn registry includes the
    canonical alias.
    """
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
    response = cast(
        "IO[bytes]", urllib.request.urlopen(request, timeout=timeout)
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    try:
        # Bound the read so a misbehaving upstream cannot OOM the parent by
        # streaming an unbounded response body (AC-08). 1 MiB is well above
        # any realistic ``tools/list`` JSON payload.
        response_data = response.read(_LIFECYCLE_MAX_RESPONSE_BYTES)
    finally:
        response.close()
    raw = response_data.decode("utf-8", errors="replace")
    if not raw:
        return []
    # The FallbackStandaloneServer responds with an SSE frame
    # ``event: message\\ndata: {json}\\n\\n``. The data payload
    # contains the JSON-RPC response. Strip the SSE envelope and
    # parse the JSON.
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
        cast(
            "str", entry_map["name"]
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        for entry in tools
        for entry_map in [cast("dict[str, object]", entry)]
        if isinstance(entry, dict) and isinstance(entry_map.get("name"), str)
    ]
