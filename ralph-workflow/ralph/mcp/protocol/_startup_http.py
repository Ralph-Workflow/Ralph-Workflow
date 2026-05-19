from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from urllib.parse import urljoin, urlparse

import httpx

from ralph.mcp.protocol._permanent_preflight_error import PermanentPreflightError
from ralph.mcp.protocol._retryable_preflight_error import RetryablePreflightError

if TYPE_CHECKING:
    from datetime import timedelta
    from typing import Protocol

    class HttpPostFn(Protocol):
        def __call__(
            self,
            url: str,
            *,
            json: JsonRpcResponse,
            headers: dict[str, str],
            timeout: float,
        ) -> httpx.Response: ...

    class HttpJsonRpcWithSessionFn(Protocol):
        def __call__(
            self,
            endpoint_or_target: str | HttpEndpointTarget,
            target_or_payload: HttpEndpointTarget | JsonRpcResponse,
            payload: JsonRpcResponse | None = None,
            *,
            session_id: str | None = None,
            post_fn: HttpPostFn = ...,
        ) -> tuple[JsonRpcResponse, str | None]: ...
else:
    HttpPostFn = object
    HttpJsonRpcWithSessionFn = object

JsonRpcResponse = dict[str, object]
_HTTP_OK = 200
_HTTP_ACCEPTED = 202


@dataclass(frozen=True)
class HttpEndpointTarget:
    address: tuple[str, int]
    host_header: str
    path: str


def _trust_env_for_http_endpoint(endpoint: str) -> bool:
    return urlparse(endpoint).scheme == "https"


def _default_http_post(
    url: str,
    *,
    json: JsonRpcResponse,
    headers: dict[str, str],
    timeout: float,
) -> httpx.Response:
    return httpx.post(
        url,
        json=json,
        headers=headers,
        timeout=timeout,
        trust_env=_trust_env_for_http_endpoint(url),
    )


def initialize_request() -> JsonRpcResponse:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ralph-preflight", "version": "0"},
        },
    }


def initialized_notification() -> JsonRpcResponse:
    return {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }


def tools_list_request() -> JsonRpcResponse:
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    }


def looks_like_legacy_sse_endpoint(endpoint: str) -> bool:
    parsed = urlparse(endpoint)
    return (parsed.path or "/").rstrip("/").endswith("/sse")


def legacy_sse_jsonrpc_exchange(
    endpoint: str,
    requests: Iterable[JsonRpcResponse],
    *,
    timeout_s: float,
) -> list[JsonRpcResponse]:
    timeout = httpx.Timeout(timeout_s, connect=min(timeout_s, 5.0))
    responses: list[JsonRpcResponse] = []
    with (
        httpx.Client(timeout=timeout, trust_env=_trust_env_for_http_endpoint(endpoint)) as client,
        client.stream("GET", endpoint, headers={"Accept": "text/event-stream"}) as stream,
    ):
        if stream.status_code != _HTTP_OK:
            raise PermanentPreflightError(
                f"legacy SSE connect failed with status '{stream.status_code}': {stream.text}"
            )
        lines = stream.iter_lines()
        message_endpoint = read_legacy_sse_message_endpoint(endpoint, lines)
        for request in requests:
            post_response = client.post(
                message_endpoint,
                json=request,
                headers={"Accept": "application/json, text/event-stream"},
            )
            if post_response.status_code not in {_HTTP_OK, _HTTP_ACCEPTED}:
                raise PermanentPreflightError(
                    "legacy SSE POST failed with status "
                    f"'{post_response.status_code}': {post_response.text}"
                )
            if "id" not in request:
                continue
            responses.append(_read_legacy_sse_jsonrpc_message(lines))
    return responses


def read_legacy_sse_message_endpoint(endpoint: str, lines: Iterable[str]) -> str:
    while True:
        event, data = _read_sse_event(lines)
        if event == "endpoint":
            return _resolve_legacy_sse_message_endpoint(endpoint, data)


def _resolve_legacy_sse_message_endpoint(endpoint: str, advertised_endpoint: str) -> str:
    if not advertised_endpoint:
        raise PermanentPreflightError("legacy SSE endpoint event missing data")
    resolved = urlparse(urljoin(endpoint, advertised_endpoint))
    endpoint_target = parse_http_endpoint(endpoint)
    resolved_target = parse_http_endpoint(resolved.geturl())
    if (
        resolved_target.address != endpoint_target.address
        or resolved_target.host_header != endpoint_target.host_header
    ):
        raise PermanentPreflightError(
            "legacy SSE endpoint event advertised cross-origin message URL"
        )
    return resolved.geturl()


def _read_legacy_sse_jsonrpc_message(lines: Iterable[str]) -> JsonRpcResponse:
    while True:
        event, data = _read_sse_event(lines)
        if event == "message":
            try:
                payload = cast("object", json.loads(data))
            except json.JSONDecodeError as exc:
                raise PermanentPreflightError(
                    f"failed to parse legacy SSE JSON-RPC payload: {exc}"
                ) from exc
            if not isinstance(payload, dict):
                raise PermanentPreflightError("legacy SSE JSON-RPC payload is not an object")
            return cast("JsonRpcResponse", payload)


def _read_sse_event(lines: Iterable[str]) -> tuple[str | None, str]:
    event_name: str | None = None
    data_parts: list[str] = []
    for line in lines:
        if line == "":
            if event_name is not None or data_parts:
                return event_name, "\n".join(data_parts)
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line.partition(":")[2].strip() or None
            continue
        if line.startswith("data:"):
            data_parts.append(line.partition(":")[2].strip())
    raise PermanentPreflightError("legacy SSE stream ended before expected event")


def preflight_http_attempt(
    endpoint: str,
    target: HttpEndpointTarget,
    required_tools: Iterable[str],
    remaining: timedelta,
    *,
    post_with_session_fn: HttpJsonRpcWithSessionFn | None = None,
) -> None:
    if looks_like_legacy_sse_endpoint(endpoint):
        responses = legacy_sse_jsonrpc_exchange(
            endpoint,
            (initialize_request(), initialized_notification(), tools_list_request()),
            timeout_s=max(remaining.total_seconds(), 0.001),
        )
        initialize_response = responses[0]
        tools_response = responses[-1]
        ensure_no_preflight_error("HTTP MCP initialize", initialize_response.get("error"))
        ensure_no_preflight_error("HTTP MCP tools/list", tools_response.get("error"))
        tools = extract_preflight_tool_names(tools_response.get("result"), "HTTP MCP")
        ensure_required_tools(required_tools, tools)
        return
    post_fn = post_with_session_fn or post_http_jsonrpc_with_session
    initialize_response, session_id = post_fn(endpoint, target, initialize_request())
    ensure_no_preflight_error("HTTP MCP initialize", initialize_response.get("error"))
    if not session_id:
        raise PermanentPreflightError("HTTP MCP initialize missing mcp-session-id header")
    notification_response, session_id = post_fn(
        endpoint,
        target,
        initialized_notification(),
        session_id=session_id,
    )
    ensure_no_preflight_error(
        "HTTP MCP notifications/initialized", notification_response.get("error")
    )
    tools_response, _ = post_fn(endpoint, target, tools_list_request(), session_id=session_id)
    ensure_no_preflight_error("HTTP MCP tools/list", tools_response.get("error"))
    tools = extract_preflight_tool_names(tools_response.get("result"), "HTTP MCP")
    ensure_required_tools(required_tools, tools)


def post_http_jsonrpc(
    endpoint_or_target: str | HttpEndpointTarget,
    target_or_payload: HttpEndpointTarget | JsonRpcResponse,
    payload: JsonRpcResponse | None = None,
) -> JsonRpcResponse:
    response_payload, _ = post_http_jsonrpc_with_session(
        endpoint_or_target,
        target_or_payload,
        payload,
    )
    return response_payload


def post_http_jsonrpc_with_session(
    endpoint_or_target: str | HttpEndpointTarget,
    target_or_payload: HttpEndpointTarget | JsonRpcResponse,
    payload: JsonRpcResponse | None = None,
    *,
    session_id: str | None = None,
    post_fn: HttpPostFn = _default_http_post,
) -> tuple[JsonRpcResponse, str | None]:
    if isinstance(endpoint_or_target, HttpEndpointTarget):
        endpoint = f"http://{endpoint_or_target.host_header}{endpoint_or_target.path}"
        assert payload is None
        payload_obj = cast("JsonRpcResponse", target_or_payload)
    else:
        endpoint = endpoint_or_target
        assert payload is not None
        payload_obj = payload

    try:
        headers = {"Accept": "application/json, text/event-stream"}
        if session_id:
            headers["mcp-session-id"] = session_id
        response = post_fn(
            endpoint,
            json=payload_obj,
            headers=headers,
            timeout=5.0,
        )
    except httpx.TransportError as exc:
        raise RetryablePreflightError(
            f"failed to connect to MCP endpoint {endpoint}: {exc}"
        ) from exc

    if response.status_code == _HTTP_ACCEPTED and not response.content.strip():
        next_session_id = cast("str | None", response.headers.get("mcp-session-id"))
        return {}, next_session_id or session_id

    if response.status_code != _HTTP_OK:
        raise PermanentPreflightError(
            f"HTTP MCP request failed with status '{response.status_code}': {response.text}"
        )
    normalized_body = _normalize_http_jsonrpc_body(response.content)
    try:
        response_payload = cast("object", json.loads(normalized_body))
    except json.JSONDecodeError as exc:
        raise PermanentPreflightError(f"failed to parse HTTP MCP response JSON: {exc}") from exc
    if not isinstance(response_payload, dict):
        raise PermanentPreflightError("failed to parse HTTP MCP response JSON: expected object")
    session_id = cast("str | None", response.headers.get("mcp-session-id"))
    return cast("JsonRpcResponse", response_payload), session_id


def _normalize_http_jsonrpc_body(body_bytes: bytes) -> bytes:
    stripped = body_bytes.strip()
    if stripped.startswith((b"event:", b"data:")):
        for line in stripped.splitlines():
            if line.startswith(b"data:"):
                return line.removeprefix(b"data:").strip()
    return stripped


def ensure_http_initialize(endpoint: str, target: HttpEndpointTarget) -> None:
    response = post_http_jsonrpc(endpoint, target, initialize_request())
    ensure_no_preflight_error("HTTP MCP initialize", response.get("error"))


def read_http_tools_list_response(endpoint: str, target: HttpEndpointTarget) -> list[str]:
    response = post_http_jsonrpc(endpoint, target, tools_list_request())
    ensure_no_preflight_error("HTTP MCP tools/list", response.get("error"))
    return extract_preflight_tool_names(response.get("result"), "HTTP MCP")


def ensure_no_preflight_error(label: str, error: object) -> None:
    if error is not None:
        raise PermanentPreflightError(f"{label} failed: {error}")


def extract_preflight_tool_names(result: object, label: str) -> list[str]:
    if not isinstance(result, Mapping):
        raise PermanentPreflightError(f"{label} tools/list response missing result")
    tools = result.get("tools")
    if not isinstance(tools, list):
        raise PermanentPreflightError("MCP tools/list result missing tools array")
    return [
        tool["name"]
        for tool in tools
        if isinstance(tool, Mapping) and isinstance(tool.get("name"), str)
    ]


def ensure_required_tools(required_tools: Iterable[str], available_tools: list[str]) -> None:
    missing = [tool for tool in required_tools if tool not in available_tools]
    if missing:
        raise PermanentPreflightError(
            f"missing required MCP tools: {missing}; available: {available_tools}"
        )


def parse_http_endpoint(endpoint: str) -> HttpEndpointTarget:
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(
            "unsupported MCP HTTP scheme "
            f"'{parsed.scheme}' for endpoint '{endpoint}' "
            "(only http:// is supported)"
        )
    host = parsed.hostname
    if host is None:
        raise ValueError(f"invalid HTTP MCP endpoint '{endpoint}': missing host")
    port = parsed.port or (80 if parsed.scheme == "http" else 443)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return HttpEndpointTarget(address=(host, port), host_header=parsed.netloc, path=path)


def probe_mcp_http_endpoint(endpoint: str, timeout: timedelta) -> None:
    timeout_s = max(0.001, timeout.total_seconds())
    target = parse_http_endpoint(endpoint)

    def _bounded_post(
        url: str, *, json: JsonRpcResponse, headers: dict[str, str], timeout: float
    ) -> httpx.Response:
        del timeout
        return httpx.post(url, json=json, headers=headers, timeout=timeout_s)

    def _bounded_post_with_session(
        endpoint_or_target: str | HttpEndpointTarget,
        target_or_payload: HttpEndpointTarget | JsonRpcResponse,
        payload: JsonRpcResponse | None = None,
        *,
        session_id: str | None = None,
        post_fn: HttpPostFn = httpx.post,
    ) -> tuple[JsonRpcResponse, str | None]:
        del post_fn
        return post_http_jsonrpc_with_session(
            endpoint_or_target,
            target_or_payload,
            payload,
            session_id=session_id,
            post_fn=_bounded_post,
        )

    preflight_http_attempt(
        endpoint, target, [], timeout, post_with_session_fn=_bounded_post_with_session
    )


__all__ = [
    "HttpEndpointTarget",
    "HttpJsonRpcWithSessionFn",
    "HttpPostFn",
    "JsonRpcResponse",
    "ensure_http_initialize",
    "ensure_no_preflight_error",
    "ensure_required_tools",
    "extract_preflight_tool_names",
    "initialize_request",
    "initialized_notification",
    "legacy_sse_jsonrpc_exchange",
    "looks_like_legacy_sse_endpoint",
    "parse_http_endpoint",
    "post_http_jsonrpc",
    "post_http_jsonrpc_with_session",
    "preflight_http_attempt",
    "probe_mcp_http_endpoint",
    "read_http_tools_list_response",
    "read_legacy_sse_message_endpoint",
    "tools_list_request",
]
