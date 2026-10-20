"""Reading a JSON-RPC ``tools/list`` response from a running MCP server.

Split out of :mod:`ralph.mcp.server.lifecycle` with the byte cap that
bounds it: the read and the reason the read is bounded belong together.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import urllib.request
from typing import IO, cast

from ralph.mcp.server._mcp_outcome_unknown_error import McpOutcomeUnknownError
from ralph.mcp.server._mcp_server_error import McpServerError

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


http_tools_list_names = _http_tools_list_names


def execute_mcp_http_request(
    port: int,
    payload: dict[str, object],
    *,
    timeout: float = 5.0,
    existing_conn: http.client.HTTPConnection | None = None,
    is_idempotent: bool = True,
) -> tuple[int, int, dict[str, object] | None]:
    """Execute an MCP HTTP JSON-RPC request with production retry and error classification.

    E3.4 contract:
      - Tries ``existing_conn`` if provided.
      - If ``existing_conn`` fails with a connection reset / disconnect and the request
        is idempotent (safe), retries EXACTLY ONCE on a fresh connection.
      - If the request is non-idempotent (e.g. tools/call mutation) and the connection is
        severed mid-flight, raises McpOutcomeUnknownError (E1.4) rather than blindly retrying.
      - If client timeout expires during a transport gap, raises a typed retryable McpServerError
        (E3.2) with failure_kind="transient".

    Returns:
        (http_status, total_attempts, parsed_json_response)
    """
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    attempts = 0

    conn = (
        existing_conn
        if existing_conn is not None
        else http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    )
    try:
        conn.request("POST", "/mcp", body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        attempts += 1
        parsed: dict[str, object] | None = None
        if raw:
            try:
                data: object = json.loads(raw.decode("utf-8", errors="replace"))
                if isinstance(data, dict):
                    parsed = cast("dict[str, object]", data)
            except Exception:
                pass
        return resp.status, attempts, parsed
    except (
        http.client.RemoteDisconnected,
        ConnectionResetError,
        BrokenPipeError,
        OSError,
    ) as exc:
        attempts += 1
        if isinstance(exc, TimeoutError):
            raise McpServerError(
                f"MCP request timed out during transport gap: {exc}",
                restart_count=0,
                failure_kind="transient",
            ) from exc
        if not is_idempotent:
            method = str(payload.get("method", ""))
            params = payload.get("params")
            tool_name: str | None = None
            if isinstance(params, dict):
                raw_name = params.get("name")
                if isinstance(raw_name, str):
                    tool_name = raw_name
            raise McpOutcomeUnknownError(
                f"Non-idempotent MCP call {method} (tool={tool_name}) severed mid-flight; outcome unknown: {exc}",
                restart_count=0,
                tool_name=tool_name,
            ) from exc
        # Safe idempotent request: retry EXACTLY ONCE on fresh connection
        fresh_conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            fresh_conn.request("POST", "/mcp", body=body, headers=headers)
            fresh_resp = fresh_conn.getresponse()
            raw = fresh_resp.read()
            attempts += 1
            parsed = None
            if raw:
                try:
                    retry_data: object = json.loads(raw.decode("utf-8", errors="replace"))
                    if isinstance(retry_data, dict):
                        parsed = cast("dict[str, object]", retry_data)
                except Exception:
                    pass
            return fresh_resp.status, attempts, parsed
        except TimeoutError as timeout_exc:
            raise McpServerError(
                f"MCP request timed out on retry during transport gap: {timeout_exc}",
                restart_count=0,
                failure_kind="transient",
            ) from timeout_exc
        finally:
            with contextlib.suppress(Exception):
                fresh_conn.close()


__all__ = [
    "_LIFECYCLE_MAX_RESPONSE_BYTES",
    "execute_mcp_http_request",
    "http_tools_list_names",
]
