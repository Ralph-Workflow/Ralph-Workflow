"""Relay sequence anomalies must not kill a healthy agent (S-4 regression).

The relay's credential is the security boundary: it is minted per run, held
only by the standalone MCP server process, and scrubbed from every agent
environment.  An event that passes the credential check is therefore from
Ralph's own relay client by construction.

Ralph's own client *can* legitimately produce a duplicate or out-of-order
sequence.  ``ActivityRelaySender.emit`` reads ``self._sequence`` into the
event, does a full socket round trip, and only then advances the counter --
while the standalone MCP server is a ``ThreadingHTTPServer`` that dispatches
concurrent ``tools/call`` requests on separate threads through the same
sender.  Two overlapping tool calls read the same sequence and both send it.

These tests pin that such an event is ignored idempotently, never latches a
supervision failure, and never stops later real activity from being
delivered.  A bad credential stays fatal.
"""

from __future__ import annotations

import threading

import pytest

from ralph.mcp.server._activity_relay import ActivityRelay, ActivityRelayError, ActivityRelaySender

_IO_TIMEOUT_SECONDS = 2.0
_RACE_THREADS = 4


def _event(credential: str, sequence: object, tool_name: str) -> dict[str, object]:
    """Build one decoded relay event exactly as the wire layer would hand it over."""
    return {"credential": credential, "sequence": sequence, "tool_name": tool_name}


def _credential(relay: ActivityRelay) -> str:
    return relay.server_environment()["RALPH_MCP_ACTIVITY_RELAY_CREDENTIAL"]


def test_duplicate_sequence_with_valid_credential_does_not_latch_receiver_error() -> None:
    """A replayed sequence from our own client is a delivery anomaly, not a forgery."""
    relay = ActivityRelay()
    try:
        observed: list[str] = []
        remove = relay.register_sink(observed.append)
        try:
            credential = _credential(relay)
            first = relay.handle_event(_event(credential, 1, "read_file"))
            duplicate = relay.handle_event(_event(credential, 1, "read_file"))
            assert first["ok"] is True
            # The duplicate is acknowledged so the sender does not fail closed.
            assert duplicate["ok"] is True
            # ...but it is not counted twice, and it does not kill the run.
            assert observed == ["read_file"]
            assert relay.snapshot().delivered_events == 1
            assert relay.ignored_events == 1
            assert relay.health_error() is None
        finally:
            remove()
    finally:
        assert relay.close() is True


def test_out_of_order_sequence_with_valid_credential_is_ignored_not_fatal() -> None:
    """An event that arrives behind an already-delivered one is dropped quietly."""
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
    """A gap must resync forward, or the racing sender would go silent forever.

    The concurrency race leaves the sender's counter permanently ahead of the
    receiver's.  If the receiver kept demanding its own next value, every later
    event would be ignored, the watchdog would see no activity at all, and the
    healthy agent would be killed for idleness instead of for forgery.
    """
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


def test_duplicate_sequence_does_not_fail_the_sending_client() -> None:
    """A stale sender keeps working: a benign anomaly must not latch its error."""
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
            sender.emit("edit_file")
            assert observed == ["read_file", "edit_file"]
            assert relay.health_error() is None
        finally:
            remove()
    finally:
        assert relay.close() is True


def test_concurrent_emits_through_one_sender_never_latch_a_relay_fault() -> None:
    """The production trigger: overlapping tools/call threads share one sender."""
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
            # The race can leave the sender's counter ahead of the relay's.
            # Real work after it must still reach the watchdog.
            sender.emit("edit_file")
            assert observed[-1] == "edit_file"
            assert relay.health_error() is None
        finally:
            remove()
    finally:
        assert relay.close() is True


def test_forged_credential_with_stale_sequence_is_still_fatal() -> None:
    """A sequence anomaly on an unauthenticated event stays hostile."""
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
    """A structurally malformed event is a protocol violation, not reordering."""
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
    """The anomaly must not poison later invocations via the register_sink gate."""
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
    """Defence in depth: a missing credential is never treated as benign."""
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
