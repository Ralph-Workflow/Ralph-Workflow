"""Server-side sender for standalone MCP activity-relay events."""

from __future__ import annotations

import json
import socket
from collections.abc import MutableMapping
from typing import TYPE_CHECKING

from ralph.mcp.server._activity_relay_error import ActivityRelayError
from ralph.mcp.server._activity_relay_protocol import decode_json_object, receive_bounded_line

if TYPE_CHECKING:
    from collections.abc import Mapping

_RELAY_HOST = "127.0.0.1"
_RELAY_IO_TIMEOUT_SECONDS = 1.0
_MAX_PORT = 65_535
_ACTIVITY_RELAY_ENDPOINT_ENV = "RALPH_MCP_ACTIVITY_RELAY_ENDPOINT"
_ACTIVITY_RELAY_CREDENTIAL_ENV = "RALPH_MCP_ACTIVITY_RELAY_CREDENTIAL"


class ActivityRelaySender:
    """Send authenticated MCP tool activity and require a parent acknowledgement."""

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
                ack = _parse_ack(receive_bounded_line(connection))
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
    env.pop(_ACTIVITY_RELAY_ENDPOINT_ENV, None)
    env.pop(_ACTIVITY_RELAY_CREDENTIAL_ENV, None)
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


__all__ = ["ActivityRelaySender", "scrub_activity_relay_environment"]


def _parse_ack(raw: bytes) -> dict[str, object]:
    """Decode the parent acknowledgement for a delivered relay event."""
    return decode_json_object(raw, label="relay acknowledgement")
