"""HTTP/SSE startup helpers for MCP server preflight checks.

This module is Ralph's ACTIVE MCP HTTP client boundary. Every JSON-RPC POST
Ralph makes to an MCP endpoint (the bridge health probe wired at
``ralph/mcp/server/lifecycle.py`` ``probe=probe_mcp_http_endpoint``, the
startup preflight ``preflight_http_attempt``, and the ``/health`` route probe)
goes through :func:`post_http_jsonrpc_with_session`, so the replay and error
classification contract lives here rather than in a test-only helper.

Builds JSON-RPC initialize/tools/list payloads, performs HTTP POST and
legacy SSE exchanges, and validates that a remote MCP endpoint exposes
the tools Ralph Workflow depends on.

Transport contract (E3.4 / E1.4 / E3.2):

* A request that failed on a connection the server had already closed is
  replayed EXACTLY ONCE, and only when the replay cannot duplicate an
  effect: either the transport failed before any byte reached the server, or
  the JSON-RPC method is on the idempotent allowlist. See
  :func:`_post_jsonrpc_with_bounded_replay` and :func:`_replay_is_safe`.
* A non-idempotent request whose outcome may already be committed raises
  :class:`~ralph.mcp.server._mcp_outcome_unknown_error.McpOutcomeUnknownError`
  rather than being replayed or reported as a plain failure, so the caller
  verifies or de-duplicates instead of blindly re-running the mutation.
* When the client deadline fires first the failure surfaces as
  :class:`RetryablePreflightError`; the caller above owns the retry loop.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast
from urllib.parse import urljoin, urlparse

import httpx

from ralph.mcp.protocol._permanent_preflight_error import PermanentPreflightError
from ralph.mcp.protocol._retryable_preflight_error import RetryablePreflightError
from ralph.mcp.server._mcp_outcome_unknown_error import McpOutcomeUnknownError

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
# Generous bound: still fail-closed, but tolerant of slow scheduling under load.
_HTTP_JSONRPC_POST_TIMEOUT_S = 20.0

#: Total attempts (the original plus replays) this client makes for ONE
#: JSON-RPC POST. E3.4 caps a severed-connection replay at exactly one extra
#: attempt, so a permanently severed endpoint costs two round trips, never
#: three. Raising it would turn a transient handover gap into a retry storm
#: against a child that is already being replaced.
_MAX_TRANSPORT_ATTEMPTS: Final[int] = 2

#: JSON-RPC methods this client may replay after a severed connection.
#: ALLOWLIST semantics on purpose: ``tools/call`` and every method added in
#: the future are treated as non-idempotent until someone audits them, so a
#: severed connection can never replay a mutation by omission.
_IDEMPOTENT_JSONRPC_METHODS: Final[frozenset[str]] = frozenset(
    {
        "initialize",
        "notifications/initialized",
        "ping",
        "prompts/list",
        "resources/list",
        "resources/templates/list",
        "tools/list",
    }
)

#: Transport failures raised BEFORE any byte of the request reached the
#: server. Nothing was processed, so replaying is safe for ANY method.
_NEVER_SENT_TRANSPORT_ERRORS: Final[tuple[type[httpx.TransportError], ...]] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
)

#: Transport failures raised when the server closed the connection without
#: answering. The request may or may not have been read, so a replay is only
#: safe for an allowlisted idempotent method.
_SEVERED_CONNECTION_TRANSPORT_ERRORS: Final[tuple[type[httpx.TransportError], ...]] = (
    httpx.RemoteProtocolError,
)

#: Transport failures raised AFTER the request was handed to the server and
#: no answer arrived. The server may already have committed the effect, so a
#: non-idempotent call raises McpOutcomeUnknownError instead of being
#: replayed or reported as a plain "failed" that invites a blind re-run.
_OUTCOME_UNKNOWN_TRANSPORT_ERRORS: Final[tuple[type[httpx.TransportError], ...]] = (
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
)


@dataclass(frozen=True)
class HttpEndpointTarget:
    """Resolved address, Host header, and path for an HTTP MCP endpoint."""

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
    """Build the JSON-RPC ``initialize`` request payload."""
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
    """Build the JSON-RPC ``notifications/initialized`` payload."""
    return {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }


def tools_list_request() -> JsonRpcResponse:
    """Build the JSON-RPC ``tools/list`` request payload."""
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    }


def looks_like_legacy_sse_endpoint(endpoint: str) -> bool:
    """Return True when ``endpoint`` ends with the legacy ``/sse`` path."""
    parsed = urlparse(endpoint)
    return (parsed.path or "/").rstrip("/").endswith("/sse")


def legacy_sse_jsonrpc_exchange(
    endpoint: str,
    requests: Iterable[JsonRpcResponse],
    *,
    timeout_s: float,
) -> list[JsonRpcResponse]:
    """Exchange a sequence of JSON-RPC messages over a legacy SSE stream.

    Connects to the SSE endpoint, reads the message endpoint URL, posts
    each request, and collects responses for requests that carry an ``id``.

    Raises:
        PermanentPreflightError: On connection failure, unexpected HTTP
            status, or malformed JSON-RPC payload.
    """
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
    """Read the SSE event that advertises the JSON-RPC message endpoint."""
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
            return cast(
                "JsonRpcResponse", payload
            )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)


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
    """Verify an HTTP MCP endpoint by performing initialize + tools/list.

    Automatically chooses the legacy SSE exchange when the endpoint path
    ends with ``/sse``; otherwise uses stateful HTTP JSON-RPC posts.

    Raises:
        PermanentPreflightError: On protocol errors or missing tools.
        RetryablePreflightError: On transient network failures.
    """
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
    post_timeout_s = min(0.5, max(0.001, remaining.total_seconds()))

    def _timeout_post(
        url: str,
        *,
        json: JsonRpcResponse,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        return _default_http_post(
            url, json=json, headers=headers, timeout=min(timeout, post_timeout_s)
        )

    def _call_post(
        ep: str | HttpEndpointTarget,
        tgt_or_pld: HttpEndpointTarget | JsonRpcResponse,
        pld: JsonRpcResponse | None = None,
        *,
        sid: str | None = None,
    ) -> tuple[JsonRpcResponse, str | None]:
        if post_with_session_fn is not None:
            return post_with_session_fn(ep, tgt_or_pld, pld, session_id=sid)
        return post_http_jsonrpc_with_session(
            ep, tgt_or_pld, pld, session_id=sid, post_fn=_timeout_post
        )

    initialize_response, session_id = _call_post(endpoint, target, initialize_request())
    ensure_no_preflight_error("HTTP MCP initialize", initialize_response.get("error"))
    if not session_id:
        raise PermanentPreflightError("HTTP MCP initialize missing mcp-session-id header")
    notification_response, session_id = _call_post(
        endpoint,
        target,
        initialized_notification(),
        sid=session_id,
    )
    ensure_no_preflight_error(
        "HTTP MCP notifications/initialized", notification_response.get("error")
    )
    tools_response, _ = _call_post(endpoint, target, tools_list_request(), sid=session_id)
    ensure_no_preflight_error("HTTP MCP tools/list", tools_response.get("error"))
    tools = extract_preflight_tool_names(tools_response.get("result"), "HTTP MCP")
    ensure_required_tools(required_tools, tools)


def jsonrpc_request_is_idempotent(payload: object) -> bool:
    """Return ``True`` when ``payload`` is a request this client may replay.

    Allowlist semantics: only methods whose server-side effect is a pure read
    (or an idempotent handshake step) are replayable. ``tools/call`` and any
    method nobody has audited are non-idempotent, so a severed connection can
    never replay a mutation.
    """
    if not isinstance(payload, Mapping):
        return False
    method = payload.get("method")
    return isinstance(method, str) and method in _IDEMPOTENT_JSONRPC_METHODS


def _tool_name_for_payload(payload: object) -> str | None:
    """Return the ``tools/call`` tool name carried by ``payload``, when present."""
    if not isinstance(payload, Mapping):
        return None
    params = payload.get("params")
    if not isinstance(params, Mapping):
        return None
    name = params.get("name")
    return name if isinstance(name, str) else None


def _replay_is_safe(exc: httpx.TransportError, *, is_idempotent: bool) -> bool:
    """Return ``True`` when replaying the request behind ``exc`` is side-effect free.

    Two arms, and only two: the transport failed before the server saw the
    request, or the request is on the idempotent allowlist. Every other
    failure - including a connection reset mid-response - is a real failure
    that must keep its existing classification and must never be replayed.
    """
    if isinstance(exc, _NEVER_SENT_TRANSPORT_ERRORS):
        return True
    return is_idempotent and isinstance(exc, _SEVERED_CONNECTION_TRANSPORT_ERRORS)


def _post_jsonrpc_with_bounded_replay(
    endpoint: str,
    *,
    payload: JsonRpcResponse,
    headers: dict[str, str],
    timeout: float,
    post_fn: HttpPostFn,
    is_idempotent: bool,
) -> httpx.Response:
    """POST once, replaying EXACTLY once when the failure is safe to replay.

    Raises:
        httpx.TransportError: The last transport failure, unchanged, so the
            caller classifies the real httpx class rather than a wrapped one.
    """
    attempts = 0
    while True:
        attempts += 1
        try:
            return post_fn(endpoint, json=payload, headers=headers, timeout=timeout)
        except httpx.TransportError as exc:
            if attempts >= _MAX_TRANSPORT_ATTEMPTS or not _replay_is_safe(
                exc, is_idempotent=is_idempotent
            ):
                raise


def post_http_jsonrpc(
    endpoint_or_target: str | HttpEndpointTarget,
    target_or_payload: HttpEndpointTarget | JsonRpcResponse,
    payload: JsonRpcResponse | None = None,
) -> JsonRpcResponse:
    """Post a single JSON-RPC request and return the response payload."""
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
    """Post a JSON-RPC request and return the response plus session id.

    Accepts either an endpoint URL plus explicit payload, or an
    ``HttpEndpointTarget`` plus payload. Propagates the
    ``mcp-session-id`` header when the server returns one.

    A severed connection is replayed exactly once when the replay is
    side-effect free (see :func:`_replay_is_safe`).

    Raises:
        RetryablePreflightError: On transport-level failures.
        McpOutcomeUnknownError: When a NON-idempotent request failed after it
            was dispatched, so its effect may already be committed.
        PermanentPreflightError: On HTTP error status or unparseable JSON.
    """
    if isinstance(endpoint_or_target, HttpEndpointTarget):
        endpoint = f"http://{endpoint_or_target.host_header}{endpoint_or_target.path}"
        assert payload is None
        payload_obj = cast(
            "JsonRpcResponse", target_or_payload
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    elif isinstance(target_or_payload, HttpEndpointTarget):
        endpoint = str(endpoint_or_target)
        assert payload is not None
        payload_obj = payload
    else:
        endpoint = str(endpoint_or_target)
        payload_obj = target_or_payload if payload is None else payload

    is_idempotent = jsonrpc_request_is_idempotent(payload_obj)
    try:
        headers = {"Accept": "application/json, text/event-stream"}
        if session_id:
            headers["mcp-session-id"] = session_id
        response = _post_jsonrpc_with_bounded_replay(
            endpoint,
            payload=payload_obj,
            headers=headers,
            timeout=_HTTP_JSONRPC_POST_TIMEOUT_S,
            post_fn=post_fn,
            is_idempotent=is_idempotent,
        )
    except httpx.TransportError as exc:
        if not is_idempotent and isinstance(exc, _OUTCOME_UNKNOWN_TRANSPORT_ERRORS):
            raise McpOutcomeUnknownError(
                f"non-idempotent MCP request to {endpoint} was dispatched but never "
                f"answered ({exc}); outcome unknown, replay forbidden",
                restart_count=0,
                tool_name=_tool_name_for_payload(payload_obj),
            ) from exc
        raise RetryablePreflightError(
            f"failed to connect to MCP endpoint {endpoint}: {exc}"
        ) from exc

    if response.status_code == _HTTP_ACCEPTED and not response.content.strip():
        next_session_id = cast(
            "str | None", response.headers.get("mcp-session-id")
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
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
    session_id = cast(
        "str | None", response.headers.get("mcp-session-id")
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    return (
        cast("JsonRpcResponse", response_payload),
        session_id,
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)


def _normalize_http_jsonrpc_body(body_bytes: bytes) -> bytes:
    stripped = body_bytes.strip()
    if stripped.startswith((b"event:", b"data:")):
        for line in stripped.splitlines():
            if line.startswith(b"data:"):
                return line.removeprefix(b"data:").strip()
    return stripped


def ensure_http_initialize(endpoint: str, target: HttpEndpointTarget) -> None:
    """Post ``initialize`` and raise if the response contains an error."""
    response = post_http_jsonrpc(endpoint, target, initialize_request())
    ensure_no_preflight_error("HTTP MCP initialize", response.get("error"))


def read_http_tools_list_response(endpoint: str, target: HttpEndpointTarget) -> list[str]:
    """Post ``tools/list`` and return the available tool names."""
    response = post_http_jsonrpc(endpoint, target, tools_list_request())
    ensure_no_preflight_error("HTTP MCP tools/list", response.get("error"))
    return extract_preflight_tool_names(response.get("result"), "HTTP MCP")


def ensure_no_preflight_error(label: str, error: object) -> None:
    """Raise ``PermanentPreflightError`` when ``error`` is not None."""
    if error is not None:
        raise PermanentPreflightError(f"{label} failed: {error}")


def extract_preflight_tool_names(result: object, label: str) -> list[str]:
    """Extract tool names from a JSON-RPC ``tools/list`` result object."""
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
    """Raise ``PermanentPreflightError`` if any required tool is missing."""
    missing = [tool for tool in required_tools if tool not in available_tools]
    if missing:
        raise PermanentPreflightError(
            f"missing required MCP tools: {missing}; available: {available_tools}"
        )


def parse_http_endpoint(endpoint: str) -> HttpEndpointTarget:
    """Parse an HTTP MCP endpoint URL into an ``HttpEndpointTarget``.

    Raises:
        ValueError: For unsupported schemes or missing host.
    """
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
    """Probe ``endpoint`` with a bounded timeout and no required tools.

    Wraps ``preflight_http_attempt`` so the timeout is applied to every
    underlying HTTP call. Raises the same errors as
    ``preflight_http_attempt``.
    """
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
    "jsonrpc_request_is_idempotent",
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
