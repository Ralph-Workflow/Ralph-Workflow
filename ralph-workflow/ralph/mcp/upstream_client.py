from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Protocol, cast

import httpx

from ralph.mcp.upstream_models import UpstreamCallError, UpstreamTool

if TYPE_CHECKING:
    from ralph.mcp.upstream_config import UpstreamMcpServer

JsonObject = dict[str, object]
JsonRpcCaller = Callable[[str, JsonObject], JsonObject]


class UpstreamMcpClient(Protocol):
    def list_tools(self) -> list[UpstreamTool]: ...
    def call_tool(self, name: str, arguments: JsonObject) -> object: ...


class HttpUpstreamClient:
    def __init__(
        self,
        server: UpstreamMcpServer,
        *,
        caller: JsonRpcCaller | None = None,
    ) -> None:
        self._server = server
        self._caller: JsonRpcCaller = (
            caller if caller is not None else _make_http_caller(server.url or "")
        )

    def list_tools(self) -> list[UpstreamTool]:
        try:
            result = self._caller("tools/list", {})
        except UpstreamCallError:
            raise
        except Exception as exc:
            raise UpstreamCallError(
                f"upstream server '{self._server.name}' tools/list failed: {exc}"
            ) from exc
        return _parse_tools(result)

    def call_tool(self, name: str, arguments: JsonObject) -> object:
        try:
            return self._caller("tools/call", {"name": name, "arguments": arguments})
        except UpstreamCallError:
            raise
        except Exception as exc:
            raise UpstreamCallError(
                f"upstream server '{self._server.name}' tool '{name}' failed: {exc}"
            ) from exc


class StdioUpstreamClient:
    def __init__(
        self,
        server: UpstreamMcpServer,
        *,
        caller: JsonRpcCaller | None = None,
    ) -> None:
        self._server = server
        self._caller: JsonRpcCaller = (
            caller if caller is not None else _make_stdio_caller(server)
        )

    def list_tools(self) -> list[UpstreamTool]:
        try:
            result = self._caller("tools/list", {})
        except UpstreamCallError:
            raise
        except Exception as exc:
            raise UpstreamCallError(
                f"upstream server '{self._server.name}' tools/list failed: {exc}"
            ) from exc
        return _parse_tools(result)

    def call_tool(self, name: str, arguments: JsonObject) -> object:
        try:
            return self._caller("tools/call", {"name": name, "arguments": arguments})
        except UpstreamCallError:
            raise
        except Exception as exc:
            raise UpstreamCallError(
                f"upstream server '{self._server.name}' tool '{name}' failed: {exc}"
            ) from exc


def make_upstream_client(
    server: UpstreamMcpServer,
    *,
    caller: JsonRpcCaller | None = None,
) -> HttpUpstreamClient | StdioUpstreamClient:
    if server.transport == "http":
        return HttpUpstreamClient(server, caller=caller)
    return StdioUpstreamClient(server, caller=caller)


def _parse_tools(result: JsonObject) -> list[UpstreamTool]:
    raw_tools = result.get("tools")
    if not isinstance(raw_tools, list):
        return []
    tools: list[UpstreamTool] = []
    for item in raw_tools:
        if not isinstance(item, Mapping):
            continue
        item_map = cast("Mapping[str, object]", item)
        name = item_map.get("name")
        if not isinstance(name, str) or not name:
            continue
        description_raw = item_map.get("description")
        description = str(description_raw) if description_raw is not None else ""
        schema_raw = item_map.get("inputSchema") or item_map.get("input_schema")
        if isinstance(schema_raw, Mapping):
            input_schema: dict[str, object] = dict(cast("Mapping[str, object]", schema_raw))
        else:
            input_schema = {}
        tools.append(UpstreamTool(name=name, description=description, input_schema=input_schema))
    return tools


def _json_rpc_result(raw: object, context: str) -> JsonObject:
    if not isinstance(raw, Mapping):
        raise UpstreamCallError(f"unexpected response type from {context}")
    raw_map = cast("Mapping[str, object]", raw)
    err = raw_map.get("error")
    if err is not None:
        raise UpstreamCallError(f"JSON-RPC error from {context}: {err}")
    result = raw_map.get("result")
    if isinstance(result, Mapping):
        return dict(cast("Mapping[str, object]", result))
    return {}


def _make_http_caller(url: str) -> JsonRpcCaller:
    def _call(method: str, params: JsonObject) -> JsonObject:
        payload_obj: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
        try:
            response = httpx.post(
                url,
                content=json.dumps(payload_obj, separators=(",", ":")).encode(),
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise UpstreamCallError(f"HTTP request to '{url}' failed: {exc}") from exc
        raw: object = json.loads(response.content)
        return _json_rpc_result(raw, f"'{url}'")

    return _call


def _make_stdio_caller(server: UpstreamMcpServer) -> JsonRpcCaller:
    def _call(method: str, params: JsonObject) -> JsonObject:
        if not server.command:
            raise UpstreamCallError(
                f"upstream server '{server.name}' has no command configured"
            )
        command = [server.command, *server.args]
        initialize_payload: JsonObject = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ralph-upstream", "version": "0"},
            },
        }
        initialized_payload: JsonObject = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        method_payload: JsonObject = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": method,
            "params": params,
        }
        payload_lines = [
            json.dumps(initialize_payload, separators=(",", ":")),
            json.dumps(initialized_payload, separators=(",", ":")),
            json.dumps(method_payload, separators=(",", ":")),
        ]
        payload = "\n".join(payload_lines) + "\n"
        env: dict[str, str] = {**os.environ, **server.env}
        proc = subprocess.run(
            command,
            input=payload.encode(),
            capture_output=True,
            env=env,
            check=False,
        )
        if proc.returncode != 0:
            raise UpstreamCallError(
                f"upstream server '{server.name}' process exited {proc.returncode}"
            )
        stdout_lines = [line for line in proc.stdout.decode().splitlines() if line.strip()]
        if not stdout_lines:
            raise UpstreamCallError(f"upstream server '{server.name}' returned no JSON-RPC output")
        raw: object = json.loads(stdout_lines[-1])
        return _json_rpc_result(raw, f"'{server.name}'")

    return _call


__all__ = [
    "HttpUpstreamClient",
    "JsonObject",
    "JsonRpcCaller",
    "StdioUpstreamClient",
    "UpstreamCallError",
    "UpstreamMcpClient",
    "make_upstream_client",
]
