"""R4 / R3: preflight McpServerError carries last_stage, elapsed_s, and restart_count; stale predecessor stage is unreadable."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.mcp.server._mcp_health_event import (
    RestartReason,
    get_restart_decisions,
    record_restart_decision,
)
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._start_stage_tracker import (
    RUNTIME_STAGES,
    read_last_stage,
    record,
    reset_spawn_id,
    stage_file_path,
)

pytestmark = pytest.mark.subprocess_e2e


# ---------------------------------------------------------------------------
# R4: preflight McpServerError carries last_stage and elapsed_s
# ---------------------------------------------------------------------------


def test_preflight_mcp_server_error_carries_last_stage_and_elapsed_s() -> None:
    """A preflight McpServerError raised by the supervisor carries the child's last_stage and elapsed_s."""
    err = McpServerError(
        "MCP server endpoint did not become ready",
        restart_count=0,
        last_stage="upstream_catalog_or_discovery_done",
        elapsed_s=4.25,
    )
    assert err.last_stage == "upstream_catalog_or_discovery_done"
    assert err.elapsed_s == 4.25
    assert err.restart_count == 0


def test_preflight_mcp_server_error_elapsed_s_default_is_none() -> None:
    """elapsed_s defaults to None when not provided (backward-compatible)."""
    err = McpServerError("preflight timeout", restart_count=0, last_stage="config_loaded")
    assert err.elapsed_s is None


def test_preflight_mcp_server_error_last_stage_none_falls_back_to_unknown(tmp_path: Path) -> None:
    """When last_stage is None (stage file absent), the consumer falls back to 'last_stage_unknown'."""
    # Simulate the consumer-side fallback logic
    last_stage: str | None = read_last_stage(tmp_path)
    last_stage_str = last_stage if last_stage is not None else "last_stage_unknown"
    assert last_stage_str == "last_stage_unknown"


# ---------------------------------------------------------------------------
# R4: stale predecessor stage cannot be read by next child
# ---------------------------------------------------------------------------


def test_stale_predecessor_stage_unreadable_by_new_spawn_id(tmp_path: Path) -> None:
    """Child A's stage is not visible to Child B with a different spawn ID.

    Spawn A writes its final stage. Spawn B (different spawn ID) calls
    read_last_stage on the same workspace root and must receive None,
    because the stage file path includes the spawn identity.
    """
    # Spawn A records its final stage
    record(tmp_path, RUNTIME_STAGES[-1])
    assert read_last_stage(tmp_path) == RUNTIME_STAGES[-1]

    # Spawn B: simulate a different spawn by resetting the global ID
    old_spawn_id = stage_file_path(tmp_path).name.split(".")[-1]
    reset_spawn_id()
    try:
        # The new spawn's path is different, so no file is found
        assert read_last_stage(tmp_path) is None
    finally:
        # Restore original spawn_id to not pollute global state
        import ralph.mcp.server._start_stage_tracker as m

        m._spawn_id = old_spawn_id


def test_same_spawn_id_records_overwrite_stale(tmp_path: Path) -> None:
    """Within the same spawn, fast restarts overwrite the stage file in-place."""
    record(tmp_path, RUNTIME_STAGES[1])
    assert read_last_stage(tmp_path) == RUNTIME_STAGES[1]

    record(tmp_path, RUNTIME_STAGES[3])
    assert read_last_stage(tmp_path) == RUNTIME_STAGES[3]

    # No stale data from earlier in the same spawn
    assert read_last_stage(tmp_path) != RUNTIME_STAGES[1]


# ---------------------------------------------------------------------------
# R3: record_restart_decision captures reason + diagnostics
# ---------------------------------------------------------------------------


def test_record_restart_decision_process_exited_has_exit_status() -> None:
    """PROCESS_EXITED decision records exit_status from process.poll()."""
    record_restart_decision(
        reason=RestartReason.PROCESS_EXITED,
        exit_status=137,
        stage="ready_to_serve",
    )
    decisions = get_restart_decisions()
    last = decisions[-1]
    assert last.reason is RestartReason.PROCESS_EXITED
    assert last.exit_status == 137
    assert last.stage == "ready_to_serve"


def test_record_restart_decision_probe_failed_has_probe_duration() -> None:
    """PROBE_FAILED decision records probe_duration_s."""
    record_restart_decision(
        reason=RestartReason.PROBE_FAILED,
        probe_duration_s=0.812,
        stage="registry_built",
    )
    decisions = get_restart_decisions()
    last = decisions[-1]
    assert last.reason is RestartReason.PROBE_FAILED
    assert last.probe_duration_s == 0.812
    assert last.stage == "registry_built"


def test_record_restart_decision_all_stages_have_stage_label() -> None:
    """Every restart decision carries the stage label that was current at decision time."""
    for stage_label in RUNTIME_STAGES:
        record_restart_decision(
            reason=RestartReason.PROCESS_EXITED,
            exit_status=1,
            stage=stage_label,
        )
    decisions = get_restart_decisions()
    recent = decisions[-len(RUNTIME_STAGES) :]
    for i, stage_label in enumerate(RUNTIME_STAGES):
        assert recent[i].stage == stage_label


# ---------------------------------------------------------------------------
# R4 / R3: stage recording + restart decision integration
# ---------------------------------------------------------------------------


def test_record_stage_and_decision_are_independent() -> None:
    """record_restart_decision does not pollute the stage availability log."""
    from ralph.mcp.server._mcp_health_event import _EVENTS

    initial_len = len(_EVENTS)
    record_restart_decision(
        reason=RestartReason.PROBE_FAILED,
        probe_duration_s=0.1,
        stage="session_ready",
    )
    # The stage event log is separate from the restart decision log
    assert len(_EVENTS) == initial_len
