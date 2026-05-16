"""HTTP and stdio clients for proxying calls to upstream MCP servers.

Provides ``HttpUpstreamClient`` and ``StdioUpstreamClient``, both implementing
``UpstreamMcpClient``. ``make_upstream_client`` selects the right implementation
from the server's transport field. Internal helpers handle JSON-RPC framing,
legacy SSE endpoints, and multimodal content-block normalization.

Multimodal normalization is done at the registry level via
``normalize_upstream_content_blocks()``, not inside individual clients.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Protocol, cast

import httpx

from ralph.mcp.multimodal.artifacts import infer_modality_and_mime
from ralph.mcp.multimodal.resources import MediaEntryExtras, MediaSource, build_media_identity
from ralph.mcp.protocol.startup import (
    initialize_request,
    initialized_notification,
    legacy_sse_jsonrpc_exchange,
    looks_like_legacy_sse_endpoint,
)
from ralph.mcp.upstream.models import UpstreamCallError, UpstreamTool
from ralph.process.manager import SpawnOptions, get_process_manager

if TYPE_CHECKING:
    from ralph.mcp.multimodal.resources import MediaManifest
    from ralph.mcp.upstream.config import UpstreamMcpServer

JsonObject = dict[str, object]
JsonRpcCaller = Callable[[str, JsonObject], JsonObject]


class UpstreamMcpClient(Protocol):
    """Protocol satisfied by both HTTP and stdio upstream MCP client implementations."""

    def list_tools(self) -> list[UpstreamTool]: ...
    def call_tool(self, name: str, arguments: JsonObject) -> object: ...


class HasMediaManifest(Protocol):
    """Protocol for upstream clients that expose a media artifact manifest."""

    @property
    def media_manifest(self) -> MediaManifest: ...


class HttpUpstreamClient:
    """Upstream MCP client that communicates over HTTP JSON-RPC."""

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
        return result


class StdioUpstreamClient:
    """Upstream MCP client that communicates over stdio with a subprocess."""

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
        return result


def make_upstream_client(
    server: UpstreamMcpServer,
    *,
    caller: JsonRpcCaller | None = None,
) -> HttpUpstreamClient | StdioUpstreamClient:
    """Instantiate the appropriate upstream client for the server's transport."""
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


_UPSTREAM_MEDIA_BLOCK_TYPES: frozenset[str] = frozenset(
    {"image", "audio", "video", "pdf", "document"}
)

_MAX_EXTENSION_LEN = 6

_DEFAULT_MIME_BY_BLOCK_TYPE: dict[str, str] = {
    "image": "image/png",
    "audio": "audio/mpeg",
    "video": "video/mp4",
    "pdf": "application/pdf",
    "document": "application/octet-stream",
}


def _modality_from_mime(mime_type: str) -> str | None:
    """Return modality for a MIME type by prefix, or None if unrecognized."""
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type == "application/pdf":
        return "pdf"
    if mime_type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ):
        return "document"
    return None


def _extract_mime(block: Mapping[str, object], block_type: str) -> str:
    """Extract MIME type from an upstream block, falling back by block_type."""
    mime = block.get("mimeType")
    if isinstance(mime, str) and mime:
        return mime
    source = block.get("source")
    if isinstance(source, Mapping):
        media_type = source.get("media_type")
        if isinstance(media_type, str) and media_type:
            return media_type
    upstream_uri = _extract_uri(block)
    if upstream_uri:
        stem = upstream_uri.split("?")[0].split("#")[0]
        if "." in stem:
            ext = "." + stem.rsplit(".", 1)[-1]
            if len(ext) <= _MAX_EXTENSION_LEN:
                inferred = infer_modality_and_mime(ext)
                if inferred:
                    return inferred[1]
    return _DEFAULT_MIME_BY_BLOCK_TYPE.get(block_type, "application/octet-stream")


def _extract_uri(block: Mapping[str, object]) -> str | None:
    """Extract URI from URI-backed upstream block, or None if not URI-backed."""
    uri = block.get("uri")
    if isinstance(uri, str) and uri:
        return uri
    source = block.get("source")
    if isinstance(source, Mapping):
        src_uri = source.get("uri")
        if isinstance(src_uri, str) and src_uri:
            return src_uri
    return None


def _extract_data(block: Mapping[str, object]) -> bytes | None:
    """Extract raw bytes from embedded-data upstream block, or None if not embedded."""
    data = block.get("data")
    if isinstance(data, str) and data:
        try:
            return base64.b64decode(data)
        except Exception:
            return None
    source = block.get("source")
    if isinstance(source, Mapping):
        src_data = source.get("data")
        if isinstance(src_data, str) and src_data:
            try:
                return base64.b64decode(src_data)
            except Exception:
                return None
    return None


def _get_block_title(block: Mapping[str, object], tool_name: str, idx: int, block_type: str) -> str:
    """Get display title for a normalized block."""
    title = block.get("title") or block.get("name")
    if isinstance(title, str) and title:
        return title
    return f"{tool_name}_{block_type}_{idx}"


def _normalize_media_block(
    block: Mapping[str, object],
    block_type: str,
    idx: int,
    server_name: str,
    tool_name: str,
    _session: HasMediaManifest | None,
) -> dict[str, object]:
    """Normalize an upstream media block into a resource_reference content block.

    Handles image/audio/video/pdf/document blocks with either URI-backed or
    embedded-data shapes:

    - Embedded-data blocks (with 'data'/'source.data'): bytes are stored in
      the session manifest as a Ralph-owned ralph://media/... artifact.
      Delivery is 'resource_reference_replay' — the agent can call read_media
      with the returned URI to retrieve the artifact.

    - URI-backed blocks (with 'uri'/'source.uri'): the original upstream URI is
      preserved as-is. Delivery is 'resource_reference' — the URI points to
      an external resource, not a Ralph-owned artifact.
    """
    mime_type = _extract_mime(block, block_type)

    derived_modality = _modality_from_mime(mime_type)
    if derived_modality is not None and derived_modality != block_type:
        raise UpstreamCallError(
            f"upstream server '{server_name}' tool '{tool_name}' returned "
            f"content block (type='{block_type}') with inconsistent MIME type '{mime_type}' "
            f"(derived modality '{derived_modality}' != declared type '{block_type}') "
            f"at index {idx}."
        )

    title = _get_block_title(block, tool_name, idx, block_type)
    upstream_uri = _extract_uri(block)
    raw_bytes = _extract_data(block)

    if raw_bytes is not None:
        if _session is None:
            raise UpstreamCallError(
                f"upstream server '{server_name}' tool '{tool_name}' returned "
                f"embedded {block_type} content block at index {idx} "
                f"but no active session is available to store the artifact bytes. "
                f"Embedded media requires an active session manifest."
            )
        entry = _session.media_manifest.add(
            title=title,
            mime_type=mime_type,
            modality=block_type,
            raw_bytes=raw_bytes,
            extras=MediaEntryExtras(
                identity_key=build_media_identity(
                    modality=block_type,
                    mime_type=mime_type,
                    title=title,
                    source=MediaSource(raw_bytes=raw_bytes),
                ),
            ),
        )
        uri = entry.uri
        delivery = "resource_reference_replay"
    elif upstream_uri is not None:
        uri = upstream_uri
        delivery = "resource_reference"
    else:
        raise UpstreamCallError(
            f"upstream server '{server_name}' tool '{tool_name}' returned "
            f"content block (type='{block_type}') at index {idx} with neither "
            f"'uri'/'source.uri' nor 'data'/'source.data' — cannot normalize."
        )

    return {
        "type": "resource_reference",
        "uri": uri,
        "mimeType": mime_type,
        "title": title,
        "modality": block_type,
        "delivery": delivery,
    }


def _get_content_list(result: JsonObject) -> list[object] | None:
    """Extract content list from result, returning None if not a valid list of blocks."""
    content = result.get("content")
    if not isinstance(content, list):
        return None
    return list(content)


def normalize_upstream_content_blocks(
    result: JsonObject,
    server_name: str,
    tool_name: str,
    session: HasMediaManifest | None = None,
) -> None:
    """Normalize upstream tool result content blocks into the multimodal contract.

    - text blocks: pass through unchanged.
    - resource_reference blocks: pass through unchanged.
    - image/audio/video/pdf/document blocks: normalized to resource_reference.
      URI-backed blocks preserve the upstream URI; embedded-data blocks store
      bytes in the session manifest (ralph://media/... URI) when available.
    - Other types: raise UpstreamCallError with a clear explanation.

    Modifies the result dict in place.
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
        elif block_type in _UPSTREAM_MEDIA_BLOCK_TYPES:
            normalized.append(
                _normalize_media_block(block, block_type, idx, server_name, tool_name, session)
            )
        else:
            raise UpstreamCallError(
                f"upstream server '{server_name}' tool '{tool_name}' returned "
                f"unsupported content block (type='{block_type}') at index {idx}. "
                f"Accepted types: text, resource_reference, "
                f"image, audio, video, pdf, document."
            )

    result["content"] = normalized


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
            SpawnOptions(
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                label=f"upstream:{server.name}",
            ),
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
    "HasMediaManifest",
    "HttpUpstreamClient",
    "JsonObject",
    "JsonRpcCaller",
    "StdioUpstreamClient",
    "UpstreamCallError",
    "UpstreamMcpClient",
    "make_upstream_client",
    "normalize_upstream_content_blocks",
]
