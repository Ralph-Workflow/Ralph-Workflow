"""Authenticated, bounded activity relay for standalone MCP servers.

The normal MCP activity sink is process-local.  A standalone MCP server runs in
another process, so conflict-resolution supervision uses this small parent-owned
loopback relay to carry a ``tools/call`` event back to the invocation reader.
Every event is authenticated, sequence checked, and acknowledged before the
server accepts it as liveness.  A relay fault is supervision infrastructure
failure, not an idle verdict.
"""

from __future__ import annotations

import json
import secrets
import socket
import threading
from collections import deque
from collections.abc import MutableMapping
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_RELAY_HOST = "127.0.0.1"
_RELAY_ACCEPT_TIMEOUT_SECONDS = 0.1
_RELAY_IO_TIMEOUT_SECONDS = 1.0
_RELAY_MAX_MESSAGE_BYTES = 4096
_RELAY_PENDING_EVENTS = 64
_MAX_TOOL_NAME_LENGTH = 512
_MAX_PORT = 65_535
_ACTIVITY_RELAY_ENDPOINT_ENV = "RALPH_MCP_ACTIVITY_RELAY_ENDPOINT"
_ACTIVITY_RELAY_CREDENTIAL_ENV = "RALPH_MCP_ACTIVITY_RELAY_CREDENTIAL"
RELAY_CONTROL_ENV_NAMES: frozenset[str] = frozenset(
    {_ACTIVITY_RELAY_ENDPOINT_ENV, _ACTIVITY_RELAY_CREDENTIAL_ENV}
)


class ActivityRelayError(RuntimeError):
    """A typed failure in the conflict-resolution liveness relay."""


@dataclass(frozen=True)
class ActivityRelaySnapshot:
    """Bounded relay health and delivery evidence for diagnostics."""

    running: bool
    receiver_error: str | None
    sender_error: str | None
    delivered_events: int


class ActivityRelay:
    """Parent-owned receiver for authenticated MCP tool activity.

    The relay is deliberately only created for conflict-resolution sessions.
    Ordinary MCP sessions keep their existing fail-soft, in-process activity
    behavior.  The one-time credential is known to the MCP server process but
    is removed at its bootstrap; agent processes never receive relay controls.
    """

    def __init__(self) -> None:
        self._credential = secrets.token_urlsafe(32)
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind((_RELAY_HOST, 0))
        self._listener.listen(_RELAY_PENDING_EVENTS)
        self._listener.settimeout(_RELAY_ACCEPT_TIMEOUT_SECONDS)
        bound_address = cast("tuple[str, int]", self._listener.getsockname())
        host, port = bound_address
        self._endpoint = f"{host}:{port}"
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._sink: Callable[[str], None] | None = None
        self._active = False
        self._registered_once = False
        self._pending_tools: deque[str] = deque(maxlen=_RELAY_PENDING_EVENTS)  # bounded-accumulator-ok: FIFO cap
        self._receiver_error: str | None = None
        self._sender_error: str | None = None
        self._next_sequence = 1
        self._delivered_events = 0
        self._recent_tools: deque[str] = deque(maxlen=_RELAY_PENDING_EVENTS)  # bounded-accumulator-ok: FIFO cap
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def endpoint(self) -> str:
        """Return the private parent listener endpoint for server bootstrap."""
        return self._endpoint

    def server_environment(self) -> dict[str, str]:
        """Return only the controls the standalone MCP server needs at bootstrap."""
        return {
            _ACTIVITY_RELAY_ENDPOINT_ENV: self._endpoint,
            _ACTIVITY_RELAY_CREDENTIAL_ENV: self._credential,
        }

    def register_sink(self, sink: Callable[[str], None]) -> Callable[[], None]:
        """Attach the active invocation's watchdog sink and return its remover."""
        with self._lock:
            if self._receiver_error is not None:
                raise ActivityRelayError(self._receiver_error)
            self._sink = sink
            self._active = True
            self._registered_once = True
            pending_tools = tuple(self._pending_tools)
            self._pending_tools.clear()
        for tool_name in pending_tools:
            sink(tool_name)

        def _remove() -> None:
            with self._lock:
                if self._sink is sink:
                    self._sink = None
                self._active = False

        return _remove

    def health_error(self) -> str | None:
        """Return the latched relay fault, if any."""
        with self._lock:
            return self._receiver_error or self._sender_error

    def snapshot(self) -> ActivityRelaySnapshot:
        """Return bounded state for invocation diagnostics."""
        with self._lock:
            return ActivityRelaySnapshot(
                running=not self._stop.is_set(),
                receiver_error=self._receiver_error,
                sender_error=self._sender_error,
                delivered_events=self._delivered_events,
            )

    def close(self) -> bool:
        """Stop intake, close the listener, and join its daemon thread boundedly."""
        self._stop.set()
        with self._lock:
            self._active = False
            self._sink = None
        with suppress(OSError):
            self._listener.close()
        self._thread.join(timeout=_RELAY_IO_TIMEOUT_SECONDS)
        return not self._thread.is_alive()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _address = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with connection:
                connection.settimeout(_RELAY_IO_TIMEOUT_SECONDS)
                self._receive_one(connection)

    def _receive_one(self, connection: socket.socket) -> None:
        try:
            raw = _recv_bounded_line(connection)
            event = _parse_event(raw)
            self._validate_event(event)
            tool_name = event["tool_name"]
            assert isinstance(tool_name, str)
            with self._lock:
                sink = self._sink
                if sink is None and self._registered_once:
                    raise ActivityRelayError("relay receiver has no active invocation sink")
                self._next_sequence += 1
                self._delivered_events += 1
                self._recent_tools.append(tool_name)
                if sink is None:
                    self._pending_tools.append(tool_name)
            if sink is not None:
                sink(tool_name)
            _send_ack(connection, {"ok": True})
        except (ActivityRelayError, OSError, TimeoutError, ValueError) as exc:
            message = f"SUPERVISION_INFRASTRUCTURE_FAILURE: activity relay receiver: {exc}"
            with self._lock:
                active = self._active
            if active:
                self._latch_receiver_error(message)
            try:
                _send_ack(connection, {"ok": False, "error": message})
            except OSError:
                return

    def _validate_event(self, event: Mapping[str, object]) -> None:
        credential = event.get("credential")
        sequence = event.get("sequence")
        tool_name = event.get("tool_name")
        if not isinstance(credential, str) or not secrets.compare_digest(credential, self._credential):
            raise ActivityRelayError("foreign or invalid relay credential")
        if not isinstance(sequence, int) or sequence != self._next_sequence:
            raise ActivityRelayError("stale or forged relay sequence")
        if not isinstance(tool_name, str) or not tool_name or len(tool_name) > _MAX_TOOL_NAME_LENGTH:
            raise ActivityRelayError("malformed relay tool event")

    def _latch_receiver_error(self, message: str) -> None:
        with self._lock:
            if self._receiver_error is None:
                self._receiver_error = message


class ActivityRelaySender:
    """Server-side sender which must receive a parent acknowledgement per event."""

    def __init__(self, endpoint: str, credential: str) -> None:
        self._host, self._port = _parse_endpoint(endpoint)
        self._credential = credential
        self._sequence = 1
        self._error: str | None = None

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> ActivityRelaySender | None:
        """Build a sender from bootstrap controls, or return None outside conflict mode."""
        endpoint = env.get(_ACTIVITY_RELAY_ENDPOINT_ENV)
        credential = env.get(_ACTIVITY_RELAY_CREDENTIAL_ENV)
        if endpoint is None and credential is None:
            return None
        if not endpoint or not credential:
            raise ActivityRelayError("incomplete relay bootstrap controls")
        return cls(endpoint, credential)

    def emit(self, tool_name: str) -> None:
        """Send one authenticated event and require the bounded acknowledgement."""
        if self._error is not None:
            raise ActivityRelayError(self._error)
        event = {
            "credential": self._credential,
            "sequence": self._sequence,
            "tool_name": tool_name,
        }
        try:
            with socket.create_connection((self._host, self._port), timeout=_RELAY_IO_TIMEOUT_SECONDS) as connection:
                connection.settimeout(_RELAY_IO_TIMEOUT_SECONDS)
                connection.sendall(json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n")
                ack = _parse_ack(_recv_bounded_line(connection))
        except (OSError, TimeoutError, ValueError) as exc:
            self._error = f"SUPERVISION_INFRASTRUCTURE_FAILURE: activity relay sender: {exc}"
            raise ActivityRelayError(self._error) from exc
        if ack.get("ok") is not True:
            error = ack.get("error")
            self._error = (
                str(error)
                if isinstance(error, str)
                else "SUPERVISION_INFRASTRUCTURE_FAILURE: activity relay acknowledgement rejected"
            )
            raise ActivityRelayError(self._error)
        self._sequence += 1

    @property
    def health_error(self) -> str | None:
        """Return the latched sender error, if delivery has failed."""
        return self._error


def scrub_activity_relay_environment[T: MutableMapping[str, str]](env: T) -> T:
    """Remove relay controls from an environment map in place and return it."""
    for name in RELAY_CONTROL_ENV_NAMES:
        env.pop(name, None)
    return env


def _parse_endpoint(endpoint: str) -> tuple[str, int]:
    host, separator, port_text = endpoint.rpartition(":")
    if not separator or host != _RELAY_HOST:
        raise ActivityRelayError("invalid private relay endpoint")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ActivityRelayError("invalid private relay port") from exc
    if not 1 <= port <= _MAX_PORT:
        raise ActivityRelayError("invalid private relay port")
    return host, port


def _recv_bounded_line(connection: socket.socket) -> bytes:
    chunks = bytearray()
    while len(chunks) <= _RELAY_MAX_MESSAGE_BYTES:
        piece = connection.recv(min(1024, _RELAY_MAX_MESSAGE_BYTES + 1 - len(chunks)))
        if not piece:
            break
        chunks.extend(piece)
        if b"\n" in piece:
            break
    if not chunks or len(chunks) > _RELAY_MAX_MESSAGE_BYTES:
        raise ActivityRelayError("missing or oversized relay message")
    line, separator, remainder = bytes(chunks).partition(b"\n")
    if not separator or remainder:
        raise ActivityRelayError("malformed relay framing")
    return line


def _parse_event(raw: bytes) -> dict[str, object]:
    try:
        return _decode_json_object(raw, label="relay event")
    except ValueError as exc:
        raise ActivityRelayError(str(exc)) from exc


def _parse_ack(raw: bytes) -> dict[str, object]:
    return _decode_json_object(raw, label="relay acknowledgement")


def _decode_json_object(raw: bytes, *, label: str) -> dict[str, object]:
    try:
        decoded = cast("object", json.loads(raw.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed {label} JSON") from exc
    value = decoded
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    typed_value: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f"{label} keys must be strings")
        typed_value[key] = cast("object", item)
    return typed_value


def _send_ack(connection: socket.socket, payload: dict[str, object]) -> None:
    connection.sendall(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")


__all__ = [
    "RELAY_CONTROL_ENV_NAMES",
    "ActivityRelay",
    "ActivityRelayError",
    "ActivityRelaySender",
    "ActivityRelaySnapshot",
    "scrub_activity_relay_environment",
]
