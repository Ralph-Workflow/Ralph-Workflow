from __future__ import annotations

import base64
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from importlib import import_module
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from types import ModuleType

import httpx

from ralph.mcp.multimodal.artifacts import infer_modality_and_mime
from ralph.mcp.multimodal.resources import (
    MediaEntryExtras,
    MediaSource,
    build_media_identity,
    new_artifact_id,
)
from ralph.mcp.protocol.startup import (
    initialize_request,
    initialized_notification,
    legacy_sse_jsonrpc_exchange,
    looks_like_legacy_sse_endpoint,
    upstream_call_timeout_seconds,
)
from ralph.mcp.upstream._has_media_manifest import HasMediaManifest
from ralph.mcp.upstream._stdio_upstream_client import StdioUpstreamClient
from ralph.mcp.upstream._upstream_mcp_client import UpstreamMcpClient
from ralph.mcp.upstream.models import UpstreamCallError, UpstreamTool

if TYPE_CHECKING:
    from ralph.mcp.upstream.config import UpstreamMcpServer
    from ralph.workspace.protocol import Workspace

JsonObject = dict[str, object]
JsonRpcCaller = Callable[[str, JsonObject], JsonObject]


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

    def list_tools(self, *, timeout_s: float | None = None) -> list[UpstreamTool]:
        caller = self._caller

        def caller_with_timeout(method: str, params: JsonObject) -> JsonObject:
            effective_timeout = (
                min(upstream_call_timeout_seconds(method), timeout_s)
                if timeout_s is not None
                else upstream_call_timeout_seconds(method)
            )
            return _with_call_timeout(caller, method, params, effective_timeout)

        try:
            result = caller_with_timeout("tools/list", {})
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


_UPSTREAM_PASSTHROUGH_BLOCK_TYPES: frozenset[str] = frozenset({"text", "resource_reference"})

UPSTREAM_MEDIA_BLOCK_TYPES: frozenset[str] = frozenset(
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
    _workspace: Workspace | None = None,
) -> dict[str, object]:
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
        identity_key = build_media_identity(
            modality=block_type,
            mime_type=mime_type,
            title=title,
            source=MediaSource(raw_bytes=raw_bytes),
        )
        cache_path = ""
        byte_loader: Callable[[], bytes | None] | None = None
        artifact_id = new_artifact_id()
        if _workspace is not None:
            try:
                _media_io_mod: ModuleType = import_module("ralph.mcp.tools.workspace._media_io")
                _media_session_mod: ModuleType = import_module(
                    "ralph.mcp.tools.workspace._media_session"
                )

                write_durable_media_cache = cast(
                    "Callable[[object, str, bytes], str]",
                    _media_io_mod.write_durable_media_cache,
                )
                _workspace_artifact_loader = cast(
                    "Callable[[object, str, str], Callable[[], bytes | None]]",
                    _media_session_mod._workspace_artifact_loader,
                )

                cache_path = write_durable_media_cache(_workspace, artifact_id, raw_bytes)
                if cache_path:
                    byte_loader = _workspace_artifact_loader(_workspace, cache_path, "")
            except Exception:
                cache_path = ""
                byte_loader = None
        entry = _session.media_manifest.add(
            title=title,
            mime_type=mime_type,
            modality=block_type,
            raw_bytes=raw_bytes,
            extras=MediaEntryExtras(
                identity_key=identity_key,
                artifact_id=artifact_id,
                cache_path=cache_path,
                byte_loader=byte_loader,
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


def _get_content_list(result: JsonObject, server_name: str, tool_name: str) -> list[object] | None:
    content = result.get("content")
    if content is None:
        return None
    if not isinstance(content, (list, tuple)):
        msg = (
            f"upstream server {server_name!r} tool {tool_name!r} returned a 'content' "
            f"field that is not a list of blocks (got {type(content).__name__}); "
            "Ralph cannot normalize it, so it is refused rather than forwarded"
        )
        raise UpstreamCallError(msg)
    return list(cast("Sequence[object]", content))


def carries_upstream_media_blocks(content: object) -> bool:
    if content is None:
        return False
    if not isinstance(content, (list, tuple)):
        return True
    for block in cast("Sequence[object]", content):
        if not isinstance(block, Mapping):
            return True
        typed_block: Mapping[str, object] = cast("Mapping[str, object]", block)
        raw_type: object = typed_block.get("type")
        if not isinstance(raw_type, str):
            return True
        if raw_type.strip().lower() not in _UPSTREAM_PASSTHROUGH_BLOCK_TYPES:
            return True
    return False


def normalize_upstream_content_blocks(
    result: JsonObject,
    server_name: str,
    tool_name: str,
    session: HasMediaManifest | None = None,
    workspace: Workspace | None = None,
) -> None:
    content_blocks = _get_content_list(result, server_name, tool_name)
    if content_blocks is None:
        return

    normalized: list[object] = []
    for idx, block in enumerate(content_blocks):
        if not isinstance(block, Mapping) or not isinstance(block.get("type"), str):
            raise UpstreamCallError(
                f"upstream server '{server_name}' tool '{tool_name}' returned a "
                f"content block with no readable 'type' at index {idx}. Every "
                f"block must declare one of: text, resource_reference, image, "
                f"audio, video, pdf, document."
            )
        block_type = str(block.get("type"))
        if block_type in ("text", "resource_reference"):
            normalized.append(block)
        elif block_type in UPSTREAM_MEDIA_BLOCK_TYPES:
            normalized.append(
                _normalize_media_block(
                    block, block_type, idx, server_name, tool_name, session, workspace
                )
            )
        else:
            raise UpstreamCallError(
                f"upstream server '{server_name}' tool '{tool_name}' returned "
                f"unsupported content block (type='{block_type}') at index {idx}. "
                f"Accepted types: text, resource_reference, "
                f"image, audio, video, pdf, document."
            )

    result["content"] = normalized


def _http_rpc_call(url: str, method: str, params: JsonObject, timeout_s: float) -> JsonObject:
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
            timeout_s=timeout_s,
        )
        return _json_rpc_result(responses[-1], f"'{url}'")
    try:
        response = httpx.post(
            url,
            content=json.dumps(payload_obj, separators=(",", ":")).encode(),
            headers={"Content-Type": "application/json"},
            timeout=timeout_s,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise UpstreamCallError(f"HTTP request to '{url}' failed: {exc}") from exc
    raw: object = json.loads(response.content)
    return _json_rpc_result(raw, f"'{url}'")


def _make_http_caller(url: str) -> JsonRpcCaller:
    def _call(method: str, params: JsonObject) -> JsonObject:
        timeout_s = upstream_call_timeout_seconds(method)
        return _http_rpc_call(url, method, params, timeout_s)

    return _call


def _with_call_timeout(
    caller: JsonRpcCaller, method: str, params: JsonObject, timeout_s: float
) -> JsonObject:

    result: dict[str, JsonObject | BaseException] = {}

    def _run() -> None:
        try:
            result["value"] = caller(method, params)
        except BaseException as exc:
            result["error"] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        raise UpstreamCallError(f"injected caller timed out after {timeout_s:.1f}s during {method}")
    error = result.get("error")
    if isinstance(error, BaseException):
        if isinstance(error, UpstreamCallError):
            raise error
        raise UpstreamCallError(f"{method} failed: {error}") from error
    value = result.get("value")
    if not isinstance(value, dict):
        raise UpstreamCallError(f"{method} returned a non-object result")
    return value


__all__ = [
    "UPSTREAM_MEDIA_BLOCK_TYPES",
    "HasMediaManifest",
    "HttpUpstreamClient",
    "JsonObject",
    "JsonRpcCaller",
    "StdioUpstreamClient",
    "UpstreamCallError",
    "UpstreamMcpClient",
    "carries_upstream_media_blocks",
    "make_upstream_client",
    "normalize_upstream_content_blocks",
]
