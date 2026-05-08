"""HTTP and stdio clients for proxying calls to upstream MCP servers.

Provides ``HttpUpstreamClient`` and ``StdioUpstreamClient``, both implementing
``UpstreamMcpClient``. ``make_upstream_client`` selects the right implementation
from the server's transport field. Internal helpers handle JSON-RPC framing,
legacy SSE endpoints, and multimodal content-block rejection.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Protocol, cast

import httpx

from ralph.mcp.multimodal.artifacts import infer_modality_and_mime
from ralph.mcp.multimodal.resources import build_media_uri, new_artifact_id
from ralph.mcp.protocol.startup import (
    initialize_request,
    initialized_notification,
    legacy_sse_jsonrpc_exchange,
    looks_like_legacy_sse_endpoint,
)
from ralph.mcp.upstream.models import UpstreamCallError, UpstreamTool
from ralph.process.manager import get_process_manager

if TYPE_CHECKING:
    from ralph.mcp.upstream.config import UpstreamMcpServer

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
            result = self._caller("tools/call", {"name": name, "arguments": arguments})
        except UpstreamCallError:
            raise
        except Exception as exc:
            raise UpstreamCallError(
                f"upstream server '{self._server.name}' tool '{name}' failed: {exc}"
            ) from exc
        _check_upstream_content_blocks(result, self._server.name, name)
        return result


class StdioUpstreamClient:
    def __init__(
        self,
        server: UpstreamMcpServer,
        *,
        caller: JsonRpcCaller | None = None,
    ) -> None:
        self._server = server
        self._caller: JsonRpcCaller = caller if caller is not None else _make_stdio_caller(server)

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
            result = self._caller("tools/call", {"name": name, "arguments": arguments})
        except UpstreamCallError:
            raise
        except Exception as exc:
            raise UpstreamCallError(
                f"upstream server '{self._server.name}' tool '{name}' failed: {exc}"
            ) from exc
        _check_upstream_content_blocks(result, self._server.name, name)
        return result


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


def _get_content_list(result: JsonObject) -> list[object] | None:
    """Extract content list from result, returning None if not a valid list of blocks."""
    content = result.get("content")
    if not isinstance(content, list):
        return None
    return list(content)


def _normalize_image_block_to_resource_reference(
    block: Mapping[str, object], idx: int, server_name: str, tool_name: str
) -> dict[str, object]:
    """Convert an upstream image block into a resource_reference content block.

    Images returned by upstream tools are normalized into the shared multimodal
    artifact contract (resource_reference) rather than being blanket-rejected.
    This preserves the multimodal information in a form that Ralph's runtime can
    route and store via the session manifest.
    """
    source = block.get("source") or {}
    if isinstance(source, Mapping):
        mime_type = str(source.get("media_type", "")) or "image/png"
    else:
        mime_type = "image/png"
    ext = "." + mime_type.split("/")[-1].split(";")[0]
    inferred = infer_modality_and_mime(ext)
    modality = inferred[0] if inferred else "image"
    uri = build_media_uri(new_artifact_id())
    return {
        "type": "resource_reference",
        "uri": uri,
        "mimeType": mime_type,
        "title": f"{tool_name}_image_{idx}",
        "modality": modality,
        "delivery": "resource_reference",
    }


def _normalize_upstream_content_blocks(
    result: JsonObject, server_name: str, tool_name: str
) -> None:
    """Normalize upstream tool result content blocks into the multimodal contract.

    - text blocks: pass through unchanged.
    - image blocks: converted to resource_reference format.
    - resource_reference blocks: pass through unchanged.
    - Other non-text types: raise UpstreamCallError with clear explanation.

    Modifies the result in place so callers see the normalized blocks.
    """
    content_blocks = _get_content_list(result)
    if content_blocks is None:
        return

    normalized: list[object] = []
    for idx, block in enumerate(content_blocks):
        if not isinstance(block, Mapping):
            normalized.append(block)
            continue
        block_type = block.get("type")
        if not isinstance(block_type, str):
            normalized.append(block)
            continue
        if block_type in ("text", "resource_reference"):
            normalized.append(block)
        elif block_type == "image":
            normalized.append(
                _normalize_image_block_to_resource_reference(block, idx, server_name, tool_name)
            )
        else:
            raise UpstreamCallError(
                f"upstream server '{server_name}' tool '{tool_name}' returned "
                f"unsupported content block (type='{block_type}') at index {idx}. "
                "Only text, image, and resource_reference blocks are accepted from upstream tools."
            )

    result["content"] = normalized


def _check_upstream_content_blocks(result: JsonObject, server_name: str, tool_name: str) -> None:
    """Normalize upstream tool result, converting multimodal blocks into the contract.

    Delegates to _normalize_upstream_content_blocks which handles image->resource_reference
    conversion and passes text/resource_reference blocks through unchanged.
    """
    _normalize_upstream_content_blocks(result, server_name, tool_name)


def _make_http_caller(url: str) -> JsonRpcCaller:
    def _call(method: str, params: JsonObject) -> JsonObject:
        payload_obj: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": method,
            "params": params,
        }
        if looks_like_legacy_sse_endpoint(url):
            responses = legacy_sse_jsonrpc_exchange(
                url,
                (initialize_request(), initialized_notification(), payload_obj),
                timeout_s=30.0,
            )
            return _json_rpc_result(responses[-1], f"'{url}'")
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
            raise UpstreamCallError(f"upstream server '{server.name}' has no command configured")
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
        handle = get_process_manager().spawn(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            label=f"upstream:{server.name}",
        )
        try:
            stdout_bytes, _stderr = handle.communicate(input=payload.encode(), timeout=30)
        except subprocess.TimeoutExpired:
            handle.terminate(grace_period_s=0)
            raise UpstreamCallError(f"upstream server '{server.name}' timed out") from None
        if (handle.returncode or 0) != 0:
            raise UpstreamCallError(
                f"upstream server '{server.name}' process exited {handle.returncode}"
            )
        stdout_str = stdout_bytes.decode() if stdout_bytes else ""
        stdout_lines = [line for line in stdout_str.splitlines() if line.strip()]
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
