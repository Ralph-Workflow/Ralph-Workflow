from __future__ import annotations

import threading

import pytest

from ralph.mcp.server._activity_relay import ActivityRelay, ActivityRelayError, ActivityRelaySender

_IO_TIMEOUT_SECONDS = 2.0
_RACE_THREADS = 4


def _event(credential: str, sequence: object, tool_name: str) -> dict[str, object]:
    return {"credential": credential, "sequence": sequence, "tool_name": tool_name}


def _credential(relay: ActivityRelay) -> str:
    return relay.server_environment()["RALPH_MCP_ACTIVITY_RELAY_CREDENTIAL"]


def test_duplicate_sequence_with_valid_credential_does_not_latch_receiver_error() -> None:
    relay = ActivityRelay()
    try:
        observed: list[str] = []
        remove = relay.register_sink(observed.append)
        try:
            credential = _credential(relay)
            first = relay.handle_event(_event(credential, 1, "read_file"))
            duplicate = relay.handle_event(_event(credential, 1, "read_file"))
            assert first["ok"] is True
            assert duplicate["ok"] is True
            assert observed == ["read_file"]
            assert relay.snapshot().delivered_events == 1
            assert relay.ignored_events == 1
            assert relay.health_error() is None
        finally:
            remove()
    finally:
        assert relay.close() is True


def test_out_of_order_sequence_with_valid_credential_is_ignored_not_fatal() -> None:
    relay = ActivityRelay()
    try:
        observed: list[str] = []
        remove = relay.register_sink(observed.append)
        try:
            credential = _credential(relay)
            relay.handle_event(_event(credential, 1, "a"))
            relay.handle_event(_event(credential, 2, "b"))
            late = relay.handle_event(_event(credential, 1, "a"))
            assert late["ok"] is True
            assert observed == ["a", "b"]
            assert relay.health_error() is None
        finally:
            remove()
    finally:
        assert relay.close() is True


def test_relay_resynchronises_after_a_forward_sequence_gap() -> None:
    relay = ActivityRelay()
    try:
        observed: list[str] = []
        remove = relay.register_sink(observed.append)
        try:
            credential = _credential(relay)
            relay.handle_event(_event(credential, 1, "a"))
            ahead = relay.handle_event(_event(credential, 7, "b"))
            follow = relay.handle_event(_event(credential, 8, "c"))
            assert ahead["ok"] is True
            assert follow["ok"] is True
            assert observed == ["a", "b", "c"]
            assert relay.health_error() is None
        finally:
            remove()
    finally:
        assert relay.close() is True


def test_concurrent_emits_through_one_sender_never_latch_a_relay_fault() -> None:
    relay = ActivityRelay()
    try:
        observed: list[str] = []
        remove = relay.register_sink(observed.append)
        try:
            sender = ActivityRelaySender.from_environment(relay.server_environment())
            assert sender is not None
            barrier = threading.Barrier(_RACE_THREADS)
            failures: list[str] = []

            def _emit() -> None:
                barrier.wait(timeout=_IO_TIMEOUT_SECONDS)
                try:
                    sender.emit("read_file")
                except ActivityRelayError as exc:
                    failures.append(str(exc))

            threads = [threading.Thread(target=_emit) for _ in range(_RACE_THREADS)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=_IO_TIMEOUT_SECONDS)
            assert failures == []
            assert observed
            assert relay.health_error() is None
            sender.emit("edit_file")
            assert observed[-1] == "edit_file"
            assert relay.health_error() is None
        finally:
            remove()
    finally:
        assert relay.close() is True


def test_forged_credential_with_stale_sequence_is_still_fatal() -> None:
    relay = ActivityRelay()
    try:
        observed: list[str] = []
        remove = relay.register_sink(observed.append)
        try:
            credential = _credential(relay)
            relay.handle_event(_event(credential, 1, "a"))
            forged = relay.handle_event(_event("forged", 1, "a"))
            assert forged["ok"] is False
            assert observed == ["a"]
            health = relay.health_error()
            assert health is not None
            assert "SUPERVISION_INFRASTRUCTURE_FAILURE" in health
            assert "credential" in health
        finally:
            remove()
    finally:
        assert relay.close() is True


def test_authenticated_event_with_non_integer_sequence_is_still_fatal() -> None:
    relay = ActivityRelay()
    try:
        observed: list[str] = []
        remove = relay.register_sink(observed.append)
        try:
            response = relay.handle_event(_event(_credential(relay), "one", "a"))
            assert response["ok"] is False
            assert observed == []
            assert relay.health_error() is not None
        finally:
            remove()
    finally:
        assert relay.close() is True


def test_registering_a_sink_after_a_benign_anomaly_still_succeeds() -> None:
    relay = ActivityRelay()
    try:
        credential = _credential(relay)
        relay.handle_event(_event(credential, 1, "a"))
        relay.handle_event(_event(credential, 1, "a"))
        observed: list[str] = []
        remove = relay.register_sink(observed.append)
        try:
            assert observed == ["a"]
        finally:
            remove()
    finally:
        assert relay.close() is True


def test_credential_free_event_is_still_fatal() -> None:
    relay = ActivityRelay()
    try:
        observed: list[str] = []
        remove = relay.register_sink(observed.append)
        try:
            response = relay.handle_event({"sequence": 1, "tool_name": "a"})
            assert response["ok"] is False
            assert observed == []
            assert relay.health_error() is not None
        finally:
            remove()
        with pytest.raises(ActivityRelayError):
            relay.register_sink(observed.append)
    finally:
        assert relay.close() is True
