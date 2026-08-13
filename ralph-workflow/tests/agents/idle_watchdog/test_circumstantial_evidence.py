"""Regression coverage for watchdog circumstantial evidence snapshots (S-5)."""

from __future__ import annotations

from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy
from ralph.agents.timeout_clock import FakeClock


def test_circumstantial_evidence_tracks_only_meaningful_agent_output() -> None:
    """S-5: byte, tool, echo, and session signals stay distinguishable and isolated."""
    clock = FakeClock(start=10.0)
    watchdog = IdleWatchdog(TimeoutPolicy(idle_timeout_seconds=30.0), clock)
    watchdog.record_invocation_start()

    baseline = watchdog.circumstantial_evidence()
    watchdog.record_any_output()
    bytes_only = watchdog.circumstantial_evidence()
    watchdog.record_prompt_echo("echo")
    echoed = watchdog.circumstantial_evidence()
    watchdog.record_session_id_capture("sess-1")
    captured = watchdog.circumstantial_evidence()

    assert baseline.has_stdout_bytes is False
    assert bytes_only.has_stdout_bytes is True
    assert bytes_only.has_meaningful_output is False
    assert echoed.has_meaningful_output is False
    assert captured.has_session_id_captured is True
    assert captured.captured_session_id == "sess-1"

    captured.has_meaningful_output = True
    captured.has_session_id_captured = False
    captured.captured_session_id = "mutated"
    fresh = watchdog.circumstantial_evidence()

    assert fresh.has_meaningful_output is False
    assert fresh.has_session_id_captured is True
    assert fresh.captured_session_id == "sess-1"

    watchdog.record_tool_use_activity()
    assert watchdog.circumstantial_evidence().has_meaningful_output is True

    watchdog.record_invocation_start()
    reset = watchdog.circumstantial_evidence()
    assert reset.has_stdout_bytes is False
    assert reset.has_meaningful_output is False
    assert reset.has_session_id_captured is False
