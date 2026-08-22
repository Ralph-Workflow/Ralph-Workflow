"""Observed liveness-source matrix for conflict resolution (S-1)."""

from __future__ import annotations

from contextvars import Context
from dataclasses import dataclass

import pytest

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy, WatchdogVerdict
from ralph.agents.idle_watchdog.timeout_policy import TimeoutProfile
from ralph.agents.timeout_clock import FakeClock
from ralph.mcp.server._activity_relay import ActivityRelay, ActivityRelaySender
from ralph.mcp.server._activity_sink import (
    invoke_active_sink,
    invoke_subagent_sink,
    reset_active_sink,
    reset_subagent_sink,
    set_active_sink,
    set_subagent_sink,
)


@dataclass(frozen=True)
class _LivenessPathObservation:
    source: str
    producer: str
    transport: str
    parent_recorder: str
    observed_verdict: str


def test_conflict_resolution_liveness_path_matrix() -> None:
    """S-1: record the source, transport, recorder, and observed verdict.

    The separately spawned MCP server has a process-local ContextVar, so a
    parent reader sink cannot see an MCP-only event without the S-4 relay.
    """
    parent_hits: list[str] = []
    token = set_active_sink(parent_hits.append)
    try:
        Context().run(invoke_active_sink, "read_file")
    finally:
        reset_active_sink(token)

    observations = (
        _LivenessPathObservation("stdout", "agent process reader", "reader callback", "record_activity", "observed"),
        _LivenessPathObservation("mcp_tool", "standalone MCP server", "process-local ContextVar", "no parent recorder", "missing across process boundary"),
        _LivenessPathObservation("subagent", "agent output strategy", "reader ContextVar", "record_subagent_work", "observed"),
        _LivenessPathObservation("workspace", "WorkspaceMonitor", "reader callback", "record_workspace_event", "observed"),
        _LivenessPathObservation("child-wait", "ordinary watchdog", "elapsed wait counter", "CHILDREN_PERSIST_TOO_LONG", "unsafe before activity-only profile"),
        _LivenessPathObservation("session-ceiling", "ordinary watchdog", "elapsed session timer", "SESSION_CEILING_EXCEEDED", "unsafe before activity-only profile"),
        _LivenessPathObservation("driver-deadline", "legacy driver", "deadline slice", "declined attempt", "removed by S-5"),
        _LivenessPathObservation("hard-stop", "legacy driver", "wall-clock wait", "ResolutionAbandonedError", "removed by S-5"),
        _LivenessPathObservation("post-exit-process-wait", "PostExitWatchdog", "elapsed exit wait", "PROCESS_EXIT_HANG", "must be disabled for activity-only"),
        _LivenessPathObservation("descendant-wait", "PostExitWatchdog", "elapsed descendant wait", "DESCENDANT_HANG", "must be disabled for activity-only"),
    )

    assert parent_hits == []
    assert {row.source for row in observations} == {
        "stdout", "mcp_tool", "subagent", "workspace", "child-wait", "session-ceiling",
        "driver-deadline", "hard-stop", "post-exit-process-wait", "descendant-wait",
    }
    assert next(row for row in observations if row.source == "mcp_tool").observed_verdict == "missing across process boundary"


class _LiveSubagentMonitor:
    """Minimal process-monitor seam that reports one quality-filtered live child."""

    def live_subagent_count(self) -> int:
        return 1


@pytest.mark.parametrize(
    ("source", "recorder"),
    [
        ("stdout", "record_activity"),
        ("mcp_tool", "record_mcp_tool_call"),
        ("subagent_output", "record_subagent_work"),
        ("subagent_liveness", "process-monitor quality-filtered liveness"),
        ("workspace", "record_workspace_event"),
    ],
)
def test_conflict_resolution_liveness_inventory_proves_standard_categories_and_conflict_transports(
    source: str,
    recorder: str,
) -> None:
    """S-1/R3: every ordinary liveness source has the same conflict deferral path.

    The test drives each real recorder or its real active-sink transport rather
    than treating a literal observation table as evidence.  A future ordinary
    source that does not reach the conflict watchdog cannot be added to this
    inventory without making this parity check fail.
    """
    standard_clock = FakeClock()
    standard = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=10.0,
            activity_evidence_ttl_seconds=30.0,
            max_session_seconds=None,
            max_waiting_on_child_seconds=1_000.0,
            no_output_at_start_seconds=None,
        ),
        standard_clock,
        process_monitor=_LiveSubagentMonitor() if source == "subagent_liveness" else None,
    )
    standard.record_invocation_start()
    standard_clock.advance(9.0)
    _produce(source, standard)
    standard_clock.advance(1.0)
    assert standard.evaluate(lambda: AgentExecutionState.ACTIVE) is WatchdogVerdict.CONTINUE, recorder

    conflict_clock = FakeClock()
    conflict = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=900.0,
            profile=TimeoutProfile.ACTIVITY_ONLY,
            max_session_seconds=900.0,
            max_waiting_on_child_seconds=900.0,
        ),
        conflict_clock,
        process_monitor=_LiveSubagentMonitor() if source == "subagent_liveness" else None,
    )
    conflict.record_invocation_start()
    for _ in range(3):
        conflict_clock.advance(899.0)
        _produce(source, conflict)
        assert conflict.evaluate(lambda: AgentExecutionState.ACTIVE) is WatchdogVerdict.CONTINUE, source


def _produce(source: str, watchdog: IdleWatchdog) -> None:
    if source == "stdout":
        watchdog.record_activity()
    elif source == "mcp_tool":
        _emit_mcp_tool(watchdog)
    elif source == "subagent_output":
        _emit_subagent_output(watchdog)
    elif source == "subagent_liveness":
        _emit_subagent_liveness(watchdog)
    elif source == "workspace":
        watchdog.record_workspace_event()
    else:
        raise AssertionError(f"unrecognized liveness source: {source}")


def _emit_mcp_tool(watchdog: IdleWatchdog) -> None:
    token = set_active_sink(lambda _name: watchdog.record_mcp_tool_call())
    try:
        invoke_active_sink("read_file")
    finally:
        reset_active_sink(token)


def _emit_subagent_output(watchdog: IdleWatchdog) -> None:
    token = set_subagent_sink(lambda line: watchdog.record_subagent_work(description=line))
    try:
        invoke_subagent_sink("progress: delegated resolver edited a conflicted path")
    finally:
        reset_subagent_sink(token)


def _emit_subagent_liveness(watchdog: IdleWatchdog) -> None:
    del watchdog


def test_conflict_resolution_regression_mcp_relay_crosses_the_process_boundary() -> None:
    """S-4/R3: standalone MCP activity reaches conflict supervision through the relay."""
    relay = ActivityRelay()
    try:
        sender = ActivityRelaySender.from_environment(relay.server_environment())
        assert sender is not None
        sender.emit("read_file")
        observed: list[str] = []
        remove = relay.register_sink(observed.append)
        try:
            assert observed == ["read_file"]
        finally:
            remove()
    finally:
        assert relay.close() is True
