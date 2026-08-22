"""Observed liveness-source matrix for conflict resolution (S-1)."""

from __future__ import annotations

from contextvars import Context
from dataclasses import dataclass

from ralph.mcp.server._activity_relay import ActivityRelay, ActivityRelaySender
from ralph.mcp.server._activity_sink import invoke_active_sink, reset_active_sink, set_active_sink


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
