"""MCP server startup helpers ported from `ralph-workflow/src/mcp_server/startup.rs`."""

from __future__ import annotations

import errno
import json
import os
import socket
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlparse

from ralph.mcp.protocol._heartbeat_policy import HeartbeatPolicy
from ralph.mcp.protocol._permanent_preflight_error import PermanentPreflightError
from ralph.mcp.protocol._preflight_error import PreflightError
from ralph.mcp.protocol._preflight_tcp_deps import PreflightTcpDeps
from ralph.mcp.protocol._retryable_preflight_error import RetryablePreflightError
from ralph.mcp.protocol._session_bridge_error import SessionBridgeError
from ralph.mcp.protocol._startup_http import (
    HttpEndpointTarget,
    HttpJsonRpcWithSessionFn,
    HttpPostFn,
    JsonRpcResponse,
    ensure_no_preflight_error,
    ensure_required_tools,
    extract_preflight_tool_names,
    initialize_request,
    initialized_notification,
    legacy_sse_jsonrpc_exchange,
    looks_like_legacy_sse_endpoint,
    parse_http_endpoint,
    post_http_jsonrpc,
    post_http_jsonrpc_with_session,
    preflight_http_attempt,
    probe_mcp_http_endpoint,
    read_legacy_sse_message_endpoint,
    tools_list_request,
)
from ralph.mcp.protocol.capability_mapping import AccessMode, drain_to_access_mode
from ralph.mcp.protocol.env import (
    MCP_PREFLIGHT_TIMEOUT_MS_ENV,
    MCP_PROBE_TIMEOUT_MS_ENV,
    MCP_SUPERVISION_INTERVAL_MS_ENV,
)
from ralph.mcp.tool_contract import visible_owned_tool_names
from ralph.workspace import Workspace

__all__ = [
    "HeartbeatPolicy",
    "HttpEndpointTarget",
    "HttpJsonRpcWithSessionFn",
    "HttpPostFn",
    "JsonRpcResponse",
    "PreflightError",
    "SessionBridgeError",
    "access_mode_for_drain",
    "ensure_no_preflight_error",
    "extract_preflight_tool_names",
    "heartbeat_policy_from_env",
    "initialize_request",
    "initialized_notification",
    "legacy_sse_jsonrpc_exchange",
    "looks_like_legacy_sse_endpoint",
    "mcp_preflight_timeout_from_env",
    "mcp_probe_timeout_from_env",
    "parse_http_endpoint",
    "parse_tcp_endpoint",
    "post_http_jsonrpc",
    "post_http_jsonrpc_with_session",
    "preflight_http_attempt",
    "preflight_http_mcp_server_tools",
    "preflight_mcp_server_tools",
    "probe_mcp_http_endpoint",
    "read_jsonrpc_response",
    "read_legacy_sse_message_endpoint",
    "tools_list_request",
    "write_jsonrpc_request",
]

if TYPE_CHECKING:
    import io
    from collections.abc import Callable, Iterable, Mapping

    from ralph.mcp.upstream.registry import UpstreamRegistry
    from ralph.policy.models import AgentsPolicy

if TYPE_CHECKING:

    class SessionLike(Protocol):
        """Minimum API surface needed from an agent session."""

        session_id: str
        run_id: str
        drain: str
        capabilities: set[str]


WorkspaceLike = Workspace


def _visible_mcp_tool_names_owned(
    session: SessionLike,
    workspace: WorkspaceLike,
    *,
    upstream_registry: UpstreamRegistry | None = None,
) -> list[str]:
    return visible_owned_tool_names(
        session,
        workspace,
        upstream_registry=upstream_registry,
        include_aliases=True,
    )


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
    """Run preflight tool verification against an HTTP MCP endpoint."""
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
    """Execute a single TCP preflight check against an MCP endpoint."""
    resolved_deps = deps or PreflightTcpDeps()
    connect_fn = resolved_deps.connect_to_endpoint_fn or connect_to_endpoint
    list_fn = resolved_deps.list_tools_fn or list_tools_for_endpoint
    sock = connect_fn(endpoint, address, remaining)
    try:
        tools = list_fn(sock, _io_timeout_budget(remaining))
        ensure_required_tools(required_tools, tools)
    finally:
        sock.close()


def connect_to_endpoint(
    endpoint: str,
    address: tuple[str, int],
    remaining: timedelta,
    *,
    connect_fn: Callable[[tuple[str, int], float], socket.socket] = socket.create_connection,
) -> socket.socket:
    """Open a TCP connection to the MCP endpoint within the given time budget."""
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
    """Map an OS-level connection error to the appropriate PreflightError subclass."""
    message = f"failed to connect to MCP endpoint {endpoint}: {error}"
    if _retryable_connect_error_kind(error.errno):
        return RetryablePreflightError(message)
    return PermanentPreflightError(message)


def list_tools_for_endpoint(sock: socket.socket, io_timeout: timedelta) -> list[str]:
    """Complete the MCP initialize handshake and return the server's tool names."""
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
    """Send a tools/list request and return the list of tool names."""
    response = send_stdio_request(sock, reader, tools_list_request())
    ensure_no_preflight_error(f"{label} tools/list", response.get("error"))
    return extract_preflight_tool_names(response.get("result"), label)


def complete_stdio_initialize(sock: socket.socket, reader: io.BufferedReader) -> None:
    """Send the initialize request and confirmation notification over stdio."""
    response = send_stdio_request(sock, reader, initialize_request())
    ensure_no_preflight_error("MCP initialize", response.get("error"))
    write_jsonrpc_request(sock, initialized_notification())


def send_stdio_request(
    sock: socket.socket, reader: io.BufferedReader, request: JsonRpcResponse
) -> JsonRpcResponse:
    """Send a JSON-RPC request over stdio and return the parsed response."""
    write_jsonrpc_request(sock, request)
    return read_jsonrpc_response(reader)


def write_jsonrpc_request(sock: socket.socket, value: JsonRpcResponse) -> None:
    """Serialise and send a JSON-RPC message with a Content-Length header."""
    body = json.dumps(value, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    sock.sendall(header + body)


def read_jsonrpc_response(reader: io.BufferedReader) -> JsonRpcResponse:
    """Read and parse a Content-Length-framed JSON-RPC response from a stream."""
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


def reconnect_http_tools_stream(
    endpoint: str, address: tuple[str, int], io_timeout: timedelta
) -> socket.socket:
    """Open a fresh TCP connection to an HTTP MCP endpoint for tool listing."""
    try:
        sock = socket.create_connection(address, timeout=io_timeout.total_seconds())
    except OSError as exc:
        raise classify_connect_error(endpoint, exc) from exc
    _configure_stream_timeouts(sock, io_timeout)
    return sock


def parse_tcp_endpoint(endpoint: str) -> tuple[str, int]:
    """Parse a tcp:// endpoint URL into a (host, port) tuple."""
    parsed = urlparse(endpoint)
    if parsed.scheme != "tcp":
        raise ValueError(f"MCP endpoint must use tcp://, got '{endpoint}'")
    host = parsed.hostname
    port = parsed.port
    if host is None or port is None:
        raise ValueError(f"invalid MCP endpoint '{endpoint}'")
    return host, port


_read_legacy_sse_message_endpoint = read_legacy_sse_message_endpoint


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


def mcp_probe_timeout_from_env(env: Mapping[str, str] | None = None) -> timedelta:
    """Return the configured MCP responsiveness probe timeout duration."""
    default = timedelta(milliseconds=5_000)
    env_map = os.environ if env is None else env
    raw = env_map.get(MCP_PROBE_TIMEOUT_MS_ENV)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return timedelta(milliseconds=max(1, parsed))


def heartbeat_policy_from_env(env: Mapping[str, str] | None = None) -> HeartbeatPolicy:
    """Return the configured MCP supervision check interval."""
    default_ms = 2000
    env_map = os.environ if env is None else env
    raw = env_map.get(MCP_SUPERVISION_INTERVAL_MS_ENV)
    if raw is None:
        return HeartbeatPolicy(interval=timedelta(milliseconds=default_ms))
    try:
        parsed = int(raw)
    except ValueError:
        return HeartbeatPolicy(interval=timedelta(milliseconds=default_ms))
    return HeartbeatPolicy(interval=timedelta(milliseconds=max(100, parsed)))


def access_mode_for_drain(
    drain: str,
    agents_policy: AgentsPolicy | None = None,
) -> AccessMode:
    """Expose the MCP access mode mapping from the Rust startup module.

    Thin public wrapper around
    :func:`ralph.mcp.protocol.capability_mapping.drain_to_access_mode`
    that re-exports the MCP access-mode mapping under the
    ``ralph.mcp.protocol.startup`` namespace. Callers that already
    depend on the startup module (heartbeat, preflight, JSON-RPC
    helpers) do not need a second import path; the mapping logic
    itself lives in the capability-mapping module.

    A drain is one of the named session-drain values declared by an
    agent policy. The returned access mode describes whether the
    MCP session opened for that drain may read only or also write
    to the workspace.

    Args:
        drain: Drain name to resolve. Accepts a :class:`SessionDrain`
            instance or a string. Unknown drain names fall through
            to ``READ_ONLY`` because the resolver cannot determine
            that a write-capable class is allowed.
        agents_policy: Optional :class:`AgentsPolicy` whose declared
            drains should be consulted. When ``None`` (the default),
            the resolver falls back to the default agent policy
            loaded by :mod:`ralph.mcp.protocol.capability_mapping`,
            which is the conservative choice for callers that have
            not parsed an ``agents.toml`` yet.

    Returns:
        :class:`ralph.mcp.protocol._access_mode.AccessMode`:
        ``READ_WRITE`` when the drain's class explicitly allows
        writes; ``READ_ONLY`` for every other case (read-only drains,
        unknown drains, or an empty ``agents_policy``).

    Side effects:
        None. The function is a pure mapping from drain name to
        access mode and does not touch the filesystem, network, or
        any runtime state.

    See also:
        :func:`ralph.mcp.protocol.capability_mapping.drain_to_access_mode`
        contains the actual implementation; this function exists for
        callers that import from the startup namespace.
    """
    return drain_to_access_mode(drain, agents_policy)
