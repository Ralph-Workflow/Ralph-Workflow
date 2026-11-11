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


def test_preflight_mcp_server_error_carries_last_stage_and_elapsed_s() -> None:
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
    err = McpServerError("preflight timeout", restart_count=0, last_stage="config_loaded")
    assert err.elapsed_s is None


def test_preflight_mcp_server_error_last_stage_none_falls_back_to_unknown(tmp_path: Path) -> None:
    last_stage: str | None = read_last_stage(tmp_path)
    last_stage_str = last_stage if last_stage is not None else "last_stage_unknown"
    assert last_stage_str == "last_stage_unknown"


def test_stale_predecessor_stage_unreadable_by_new_spawn_id(tmp_path: Path) -> None:
    record(tmp_path, RUNTIME_STAGES[-1])
    assert read_last_stage(tmp_path) == RUNTIME_STAGES[-1]

    old_spawn_id = stage_file_path(tmp_path).name.split(".")[-1]
    reset_spawn_id()
    try:
        assert read_last_stage(tmp_path) is None
    finally:
        import ralph.mcp.server._start_stage_tracker as m

        m._spawn_id = old_spawn_id


def test_same_spawn_id_records_overwrite_stale(tmp_path: Path) -> None:
    record(tmp_path, RUNTIME_STAGES[1])
    assert read_last_stage(tmp_path) == RUNTIME_STAGES[1]

    record(tmp_path, RUNTIME_STAGES[3])
    assert read_last_stage(tmp_path) == RUNTIME_STAGES[3]

    assert read_last_stage(tmp_path) != RUNTIME_STAGES[1]


def test_record_restart_decision_process_exited_has_exit_status() -> None:
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


def test_record_stage_and_decision_are_independent() -> None:
    from ralph.mcp.server._mcp_health_event import _EVENTS

    initial_len = len(_EVENTS)
    record_restart_decision(
        reason=RestartReason.PROBE_FAILED,
        probe_duration_s=0.1,
        stage="session_ready",
    )
    assert len(_EVENTS) == initial_len
