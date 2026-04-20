"""MCP server startup helpers ported from `ralph-workflow/src/mcp_server/startup.rs`."""

from __future__ import annotations

import errno
import json
import os
import socket
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlparse

import httpx

from ralph.mcp.protocol.capability_mapping import AccessMode, SessionDrain, drain_to_access_mode
from ralph.mcp.protocol.env import (
    MCP_HEARTBEAT_INTERVAL_MS_ENV,
    MCP_HEARTBEAT_MISSES_ENV,
    MCP_HEARTBEAT_RECONNECT_MS_ENV,
    MCP_PREFLIGHT_TIMEOUT_MS_ENV,
)
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.workspace import Workspace

_HTTP_OK = 200
_HTTP_ACCEPTED = 202

if TYPE_CHECKING:
    import io

    from ralph.mcp.upstream.registry import UpstreamRegistry

JsonRpcResponse = dict[str, object]


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
        post_fn: HttpPostFn = httpx.post,
    ) -> tuple[JsonRpcResponse, str | None]: ...


@dataclass(frozen=True)
class PreflightTcpDeps:
    connect_to_endpoint_fn: Callable[[str, tuple[str, int], timedelta], socket.socket] | None = None
    list_tools_fn: Callable[[socket.socket, timedelta], list[str]] | None = None


def initialize_request() -> JsonRpcResponse:
    """Build the standard JSON-RPC initialize request payload."""

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
    """Build the post-initialize notification payload."""

    return {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }


def tools_list_request() -> JsonRpcResponse:
    """Build the JSON-RPC request for tool discovery."""

    return {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    }


class SessionLike(Protocol):
    """Minimum API surface needed from an agent session."""

    session_id: str
    run_id: str
    drain: str
    capabilities: set[str]


WorkspaceLike = Workspace


class SessionBridgeLike(Protocol):
    """Protocol describing the session bridge interface used here."""

    def start(self) -> None:
        """Start accepting MCP connections."""

        ...

    def agent_endpoint_uri(self) -> str:
        """Return the agent-facing endpoint URI."""

        ...

    def endpoint_uri(self) -> str:
        """Return the raw endpoint URI used for transport-level preflight."""

        ...

    def shutdown(self) -> None:
        """Shut down the bridge."""

        ...


class SessionBridgeError(Exception):
    """Raised when the session bridge fails to start or preflight fails."""


@dataclass(frozen=True)
class HeartbeatPolicy:
    """Configuration for heartbeat enforcement."""

    interval: timedelta
    misses: int
    reconnect_interval: timedelta


class PreflightError(Exception):
    """Base class for MCP preflight failures."""


class RetryablePreflightError(PreflightError):
    """Transient preflight errors that may succeed if retried."""


class PermanentPreflightError(PreflightError):
    """Preflight errors that must abort the connection attempt."""


@dataclass(frozen=True)
class HttpEndpointTarget:
    """Parsed metadata for an HTTP MCP endpoint."""

    address: tuple[str, int]
    host_header: str
    path: str


def _visible_mcp_tool_names_owned(
    session: SessionLike,
    workspace: WorkspaceLike,
    *,
    upstream_registry: UpstreamRegistry | None = None,
) -> list[str]:
    bridge = build_ralph_tool_registry(
        session, workspace, upstream_registry=upstream_registry, mcp_config=None
    )
    return [definition.name for definition in bridge.list_definitions()]


def mcp_preflight_timeout_from_env(env: Mapping[str, str] | None = None) -> timedelta:
    """Return the configured MCP preflight timeout duration."""

    default = timedelta(milliseconds=30_000)
    env_map = os.environ if env is None else env
    raw = env_map.get(MCP_PREFLIGHT_TIMEOUT_MS_ENV)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return timedelta(milliseconds=max(1, parsed))


def preflight_mcp_server_tools(
    endpoint: str, required_tools: Iterable[str], timeout: timedelta
) -> None:
    """Ensure the MCP server reports every tool that Ralph exposes."""

    required = tuple(required_tools)
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return preflight_http_mcp_server_tools(endpoint, required, timeout)

    host, port = parse_tcp_endpoint(endpoint)
    return run_preflight_loop(
        endpoint,
        timeout,
        lambda remaining: preflight_tcp_attempt(
            endpoint,
            (host, port),
            required,
            remaining,
        ),
    )


def preflight_http_mcp_server_tools(
    endpoint: str, required_tools: Iterable[str], timeout: timedelta
) -> None:
    target = parse_http_endpoint(endpoint)
    return run_preflight_loop(
        endpoint,
        timeout,
        lambda remaining: preflight_http_attempt(endpoint, target, required_tools, remaining),
    )


def run_preflight_loop(
    endpoint: str,
    timeout: timedelta,
    attempt: Callable[[timedelta], None],
    *,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """Repeat preflight attempts until success or timeout."""

    start = monotonic_fn()
    last_error: str | None = None

    while True:
        remaining = _remaining_budget(start, timeout, monotonic_fn=monotonic_fn)
        if remaining <= timedelta(0):
            raise PermanentPreflightError(
                last_error or f"MCP preflight timed out for endpoint {endpoint} after {timeout}"
            )

        try:
            attempt(remaining)
            return
        except PermanentPreflightError:
            raise
        except RetryablePreflightError as exc:
            last_error = str(exc)
            delay = _retry_poll_delay(start, timeout, monotonic_fn=monotonic_fn)
            sleep_fn(delay.total_seconds())


def preflight_tcp_attempt(
    endpoint: str,
    address: tuple[str, int],
    required_tools: Iterable[str],
    remaining: timedelta,
    *,
    deps: PreflightTcpDeps | None = None,
) -> None:
    resolved_deps = deps or PreflightTcpDeps()
    connect_fn = resolved_deps.connect_to_endpoint_fn or connect_to_endpoint
    list_fn = resolved_deps.list_tools_fn or list_tools_for_endpoint
    sock = connect_fn(endpoint, address, remaining)
    try:
        tools = list_fn(sock, _io_timeout_budget(remaining))
        ensure_required_tools(required_tools, tools)
    finally:
        sock.close()


def preflight_http_attempt(
    endpoint: str,
    target: HttpEndpointTarget,
    required_tools: Iterable[str],
    remaining: timedelta,
    *,
    post_with_session_fn: HttpJsonRpcWithSessionFn | None = None,
) -> None:
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


def connect_to_endpoint(
    endpoint: str,
    address: tuple[str, int],
    remaining: timedelta,
    *,
    connect_fn: Callable[[tuple[str, int], float], socket.socket] = socket.create_connection,
) -> socket.socket:
    timeout = max(0.001, _connect_timeout_budget(remaining).total_seconds())
    try:
        return connect_fn(address, timeout)
    except TimeoutError as exc:
        raise RetryablePreflightError(
            f"failed to connect to MCP endpoint {endpoint}: {exc}"
        ) from exc
    except OSError as exc:
        raise classify_connect_error(endpoint, exc) from exc


def classify_connect_error(endpoint: str, error: OSError) -> PreflightError:
    message = f"failed to connect to MCP endpoint {endpoint}: {error}"
    if _retryable_connect_error_kind(error.errno):
        return RetryablePreflightError(message)
    return PermanentPreflightError(message)


def ensure_required_tools(required_tools: Iterable[str], available_tools: list[str]) -> None:
    missing = [tool for tool in required_tools if tool not in available_tools]
    if missing:
        raise PermanentPreflightError(
            f"missing required MCP tools: {missing}; available: {available_tools}"
        )


def list_tools_for_endpoint(sock: socket.socket, io_timeout: timedelta) -> list[str]:
    _configure_stream_timeouts(sock, io_timeout)
    reader = sock.makefile("rb")
    try:
        complete_stdio_initialize(sock, reader)
        return read_tools_list_response(sock, reader, "MCP")
    finally:
        reader.close()


def read_tools_list_response(
    sock: socket.socket, reader: io.BufferedReader, label: str
) -> list[str]:
    response = send_stdio_request(sock, reader, tools_list_request())
    ensure_no_preflight_error(f"{label} tools/list", response.get("error"))
    return extract_preflight_tool_names(response.get("result"), label)


def complete_stdio_initialize(sock: socket.socket, reader: io.BufferedReader) -> None:
    response = send_stdio_request(sock, reader, initialize_request())
    ensure_no_preflight_error("MCP initialize", response.get("error"))
    write_jsonrpc_request(sock, initialized_notification())


def send_stdio_request(
    sock: socket.socket, reader: io.BufferedReader, request: JsonRpcResponse
) -> JsonRpcResponse:
    write_jsonrpc_request(sock, request)
    return read_jsonrpc_response(reader)


def write_jsonrpc_request(sock: socket.socket, value: JsonRpcResponse) -> None:
    body = json.dumps(value, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    sock.sendall(header + body)


def read_jsonrpc_response(reader: io.BufferedReader) -> JsonRpcResponse:
    length = _read_content_length(reader)
    body = reader.read(length)
    if len(body) != length:
        raise PermanentPreflightError("failed to read MCP response body")
    try:
        payload = cast("object", json.loads(body))
    except json.JSONDecodeError as exc:
        raise PermanentPreflightError(f"failed to parse MCP response JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PermanentPreflightError("failed to parse MCP response JSON: expected object")
    return cast("JsonRpcResponse", payload)


def _read_content_length(reader: io.BufferedReader) -> int:
    length: int | None = None
    while True:
        line = reader.readline()
        if not line:
            raise PermanentPreflightError("MCP response closed while reading headers")
        text = line.decode("ascii", errors="ignore")
        if text == "\r\n":
            if length is None:
                raise PermanentPreflightError("MCP response missing Content-Length header")
            return length
        if text.lower().startswith("content-length:"):
            try:
                length = int(text.split(":", 1)[1].strip())
            except ValueError as exc:
                raise PermanentPreflightError("invalid Content-Length header") from exc


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
    post_fn: HttpPostFn = httpx.post,
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


def reconnect_http_tools_stream(
    endpoint: str, address: tuple[str, int], io_timeout: timedelta
) -> socket.socket:
    try:
        sock = socket.create_connection(address, timeout=io_timeout.total_seconds())
    except OSError as exc:
        raise classify_connect_error(endpoint, exc) from exc
    _configure_stream_timeouts(sock, io_timeout)
    return sock


def read_http_tools_list_response(
    endpoint: str, sock: socket.socket, target: HttpEndpointTarget
) -> list[str]:
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


def parse_tcp_endpoint(endpoint: str) -> tuple[str, int]:
    parsed = urlparse(endpoint)
    if parsed.scheme != "tcp":
        raise ValueError(f"MCP endpoint must use tcp://, got '{endpoint}'")
    host = parsed.hostname
    port = parsed.port
    if host is None or port is None:
        raise ValueError(f"invalid MCP endpoint '{endpoint}'")
    return host, port


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


def _connect_timeout_budget(remaining: timedelta) -> timedelta:
    return min(timedelta(milliseconds=500), remaining)


def _io_timeout_budget(remaining: timedelta) -> timedelta:
    return min(timedelta(seconds=2), remaining)


def _remaining_budget(
    start: float,
    timeout: timedelta,
    *,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> timedelta:
    elapsed = timedelta(seconds=max(0.0, monotonic_fn() - start))
    remainder = timeout - elapsed
    return remainder if remainder > timedelta(milliseconds=0) else timedelta(milliseconds=0)


def _retry_poll_delay(
    start: float,
    timeout: timedelta,
    *,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> timedelta:
    return min(
        timedelta(milliseconds=100),
        _remaining_budget(start, timeout, monotonic_fn=monotonic_fn),
    )


def _retryable_connect_error_kind(errno_value: int | None) -> bool:
    if errno_value is None:
        return False
    return errno_value in {
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.ECONNABORTED,
        errno.ETIMEDOUT,
        errno.EWOULDBLOCK,
        errno.EINTR,
        errno.ENOTCONN,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
    }


def _configure_stream_timeouts(sock: socket.socket, io_timeout: timedelta) -> None:
    sock.settimeout(io_timeout.total_seconds())


def heartbeat_policy_from_env(env: Mapping[str, str] | None = None) -> HeartbeatPolicy:
    env_map = os.environ if env is None else env
    interval = int(env_map.get(MCP_HEARTBEAT_INTERVAL_MS_ENV, "2000"))
    misses = max(1, int(env_map.get(MCP_HEARTBEAT_MISSES_ENV, "3")))
    reconnect = int(env_map.get(MCP_HEARTBEAT_RECONNECT_MS_ENV, "10000"))
    return HeartbeatPolicy(
        interval=timedelta(milliseconds=interval),
        misses=misses,
        reconnect_interval=timedelta(milliseconds=reconnect),
    )


def access_mode_for_drain(drain: SessionDrain | str) -> AccessMode:
    """Expose the MCP access mode mapping from the Rust startup module."""
    return drain_to_access_mode(drain)
