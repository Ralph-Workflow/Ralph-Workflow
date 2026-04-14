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

from ralph.mcp.capability_mapping import AccessMode, SessionDrain, drain_to_access_mode
from ralph.mcp.tool_bridge import build_ralph_tool_registry
from ralph.workspace import Workspace

if TYPE_CHECKING:
    import io

JsonRpcResponse = dict[str, object]


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


SessionBridgeFactory = Callable[[SessionLike, WorkspaceLike], SessionBridgeLike]


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


def start_mcp_server_for_session(
    session: SessionLike,
    workspace: WorkspaceLike,
    *,
    bridge_factory: SessionBridgeFactory | None = None,
) -> SessionBridgeLike:
    """Start the session bridge and verify that every tool is reachable."""

    if bridge_factory is None:
        raise SessionBridgeError("No session bridge factory provided")

    required_tools = _visible_mcp_tool_names_owned(session, workspace)
    bridge = bridge_factory(session, workspace)
    _ = heartbeat_policy_from_env()

    try:
        bridge.start()
    except Exception as exc:
        raise SessionBridgeError("Session bridge failed to start") from exc

    try:
        preflight_mcp_server_tools(
            bridge.agent_endpoint_uri(),
            required_tools,
            mcp_preflight_timeout_from_env(),
        )
    except PermanentPreflightError as exc:
        raise SessionBridgeError("MCP server preflight failed") from exc

    return bridge


def _visible_mcp_tool_names_owned(session: SessionLike, workspace: WorkspaceLike) -> list[str]:
    bridge = build_ralph_tool_registry(session, workspace)
    return [definition.name for definition in bridge.list_definitions()]


def mcp_preflight_timeout_from_env() -> timedelta:
    """Return the configured MCP preflight timeout duration."""

    default = timedelta(milliseconds=30_000)
    raw = os.environ.get("RALPH_MCP_PREFLIGHT_TIMEOUT_MS")
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
        lambda remaining: preflight_tcp_attempt(endpoint, (host, port), required, remaining),
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
    endpoint: str, timeout: timedelta, attempt: Callable[[timedelta], None]
) -> None:
    """Repeat preflight attempts until success or timeout."""

    start = time.monotonic()
    last_error: str | None = None

    while True:
        remaining = _remaining_budget(start, timeout)
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
            delay = _retry_poll_delay(start, timeout)
            time.sleep(delay.total_seconds())


def preflight_tcp_attempt(
    endpoint: str,
    address: tuple[str, int],
    required_tools: Iterable[str],
    remaining: timedelta,
) -> None:
    sock = connect_to_endpoint(endpoint, address, remaining)
    try:
        tools = list_tools_for_endpoint(sock, _io_timeout_budget(remaining))
        ensure_required_tools(required_tools, tools)
    finally:
        sock.close()


def preflight_http_attempt(
    endpoint: str,
    target: HttpEndpointTarget,
    required_tools: Iterable[str],
    remaining: timedelta,
) -> None:
    ensure_http_initialize(target)
    sock = reconnect_http_tools_stream(target.address, _io_timeout_budget(remaining))
    try:
        tools = read_http_tools_list_response(sock, target)
        ensure_required_tools(required_tools, tools)
    finally:
        sock.close()


def connect_to_endpoint(
    endpoint: str, address: tuple[str, int], remaining: timedelta
) -> socket.socket:
    timeout = max(0.001, _connect_timeout_budget(remaining).total_seconds())
    try:
        return socket.create_connection(address, timeout=timeout)
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


def post_http_jsonrpc(target: HttpEndpointTarget, payload: JsonRpcResponse) -> JsonRpcResponse:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = (
        f"POST {target.path} HTTP/1.1\r\n"
        f"Host: {target.host_header}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(5.0)
        sock.connect(target.address)
        sock.sendall(request + body)
        data = bytearray()
        chunk = sock.recv(4096)
        while chunk:
            data.extend(chunk)
            chunk = sock.recv(4096)
    finally:
        sock.close()

    header_end = bytes(data).find(b"\r\n\r\n")
    if header_end == -1:
        raise PermanentPreflightError("invalid HTTP MCP response: missing header terminator")
    header = bytes(data[:header_end]).decode("ascii", errors="ignore")
    body_bytes = bytes(data[header_end + 4 :])
    status_line = header.splitlines()[0] if header else ""
    if " 200 " not in status_line:
        response_body = body_bytes.decode("utf-8", errors="ignore")
        raise PermanentPreflightError(
            f"HTTP MCP request failed with status '{status_line}': {response_body}"
        )
    try:
        response_payload = cast("object", json.loads(body_bytes))
    except json.JSONDecodeError as exc:
        raise PermanentPreflightError(f"failed to parse HTTP MCP response JSON: {exc}") from exc
    if not isinstance(response_payload, dict):
        raise PermanentPreflightError("failed to parse HTTP MCP response JSON: expected object")
    return cast("JsonRpcResponse", response_payload)


def ensure_http_initialize(target: HttpEndpointTarget) -> None:
    response = post_http_jsonrpc(target, initialize_request())
    ensure_no_preflight_error("HTTP MCP initialize", response.get("error"))


def reconnect_http_tools_stream(address: tuple[str, int], io_timeout: timedelta) -> socket.socket:
    sock = socket.create_connection(address, timeout=io_timeout.total_seconds())
    _configure_stream_timeouts(sock, io_timeout)
    return sock


def read_http_tools_list_response(sock: socket.socket, target: HttpEndpointTarget) -> list[str]:
    response = post_http_jsonrpc(target, tools_list_request())
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


def _remaining_budget(start: float, timeout: timedelta) -> timedelta:
    elapsed = timedelta(seconds=max(0.0, time.monotonic() - start))
    remainder = timeout - elapsed
    return remainder if remainder > timedelta(milliseconds=0) else timedelta(milliseconds=0)


def _retry_poll_delay(start: float, timeout: timedelta) -> timedelta:
    return min(timedelta(milliseconds=100), _remaining_budget(start, timeout))


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


def heartbeat_policy_from_env() -> HeartbeatPolicy:
    interval = int(os.environ.get("RALPH_MCP_HEARTBEAT_INTERVAL_MS", "2000"))
    misses = max(1, int(os.environ.get("RALPH_MCP_HEARTBEAT_MISSES", "3")))
    reconnect = int(os.environ.get("RALPH_MCP_HEARTBEAT_RECONNECT_MS", "10000"))
    return HeartbeatPolicy(
        interval=timedelta(milliseconds=interval),
        misses=misses,
        reconnect_interval=timedelta(milliseconds=reconnect),
    )


def access_mode_for_drain(drain: SessionDrain | str) -> AccessMode:
    """Expose the MCP access mode mapping from the Rust startup module."""
    return drain_to_access_mode(drain)
