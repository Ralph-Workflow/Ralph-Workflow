from __future__ import annotations

import pytest

from ralph.mcp.server._activity_relay import ActivityRelay, ActivityRelayError, ActivityRelaySender


def test_activity_relay_delivers_authenticated_mcp_event_once_after_parent_registers() -> None:
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
    relay = ActivityRelay()
    endpoint = relay.endpoint
    env = relay.server_environment()
    assert relay.close() is True
    sender = ActivityRelaySender.from_environment(
        {**env, "RALPH_MCP_ACTIVITY_RELAY_ENDPOINT": endpoint}
    )
    assert sender is not None
    with pytest.raises(ActivityRelayError, match="SUPERVISION_INFRASTRUCTURE_FAILURE"):
        sender.emit("read_file")


def test_activity_relay_ignores_stale_sequence_without_killing_the_run() -> None:
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
            stale.emit("read_file")
            assert stale.health_error is None
            assert observed == ["read_file"]
            assert relay.snapshot().delivered_events == 1
            assert relay.ignored_events == 1
            assert relay.health_error() is None
            sender.emit("edit_file")
            assert observed == ["read_file", "edit_file"]
            assert relay.health_error() is None
        finally:
            remove()
    finally:
        assert relay.close() is True
