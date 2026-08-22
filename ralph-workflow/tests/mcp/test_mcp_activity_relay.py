"""Deterministic contracts for the standalone-MCP conflict activity relay (S-4)."""

from __future__ import annotations

import pytest

from ralph.mcp.server._activity_relay import ActivityRelay, ActivityRelayError, ActivityRelaySender


def test_activity_relay_delivers_authenticated_mcp_event_once_after_parent_registers() -> None:
    """S-4/R3: a standalone MCP-only event refreshes the parent exactly once."""
    relay = ActivityRelay()
    try:
        sender = ActivityRelaySender.from_environment(relay.server_environment())
        assert sender is not None
        sender.emit("read_file")
        observed: list[str] = []
        remove = relay.register_sink(observed.append)
        try:
            assert observed == ["read_file"]
            sender.emit("edit_file")
            assert observed == ["read_file", "edit_file"]
        finally:
            remove()
    finally:
        assert relay.close() is True


def test_activity_relay_rejects_forged_credential_without_refreshing_liveness() -> None:
    """S-4: a forged event is rejected and latches infrastructure failure."""
    relay = ActivityRelay()
    try:
        observed: list[str] = []
        remove = relay.register_sink(observed.append)
        try:
            forged = ActivityRelaySender(relay.endpoint, "forged")
            with pytest.raises(ActivityRelayError):
                forged.emit("write_file")
            assert observed == []
            assert relay.health_error() is not None
            assert "SUPERVISION_INFRASTRUCTURE_FAILURE" in relay.health_error()
        finally:
            remove()
    finally:
        assert relay.close() is True


def test_activity_relay_sender_fails_closed_when_parent_is_unavailable() -> None:
    """S-4: a missing receiver cannot be silently accepted as liveness."""
    relay = ActivityRelay()
    endpoint = relay.endpoint
    env = relay.server_environment()
    assert relay.close() is True
    sender = ActivityRelaySender.from_environment({**env, "RALPH_MCP_ACTIVITY_RELAY_ENDPOINT": endpoint})
    assert sender is not None
    with pytest.raises(ActivityRelayError, match="SUPERVISION_INFRASTRUCTURE_FAILURE"):
        sender.emit("read_file")


def test_activity_relay_rejects_stale_sequence() -> None:
    """S-4: replayed event sequences cannot refresh the watchdog twice."""
    relay = ActivityRelay()
    try:
        observed: list[str] = []
        remove = relay.register_sink(observed.append)
        try:
            sender = ActivityRelaySender.from_environment(relay.server_environment())
            assert sender is not None
            sender.emit("read_file")
            stale = ActivityRelaySender.from_environment(relay.server_environment())
            assert stale is not None
            with pytest.raises(ActivityRelayError):
                stale.emit("read_file")
            assert observed == ["read_file"]
        finally:
            remove()
    finally:
        assert relay.close() is True
