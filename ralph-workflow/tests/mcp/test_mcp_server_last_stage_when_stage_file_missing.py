from __future__ import annotations

import uuid
from pathlib import Path

from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._start_stage_tracker import (
    read_last_stage,
    record,
    stage_file_path,
)


def test_missing_stage_file_returns_none(tmp_path: Path) -> None:
    fake_id = str(uuid.uuid4())
    assert read_last_stage(tmp_path, spawn_id=fake_id) is None


def test_record_and_read_with_same_spawn_id(tmp_path: Path) -> None:
    spawn_id = str(uuid.uuid4())
    record(tmp_path, "config_loaded")
    record(tmp_path, "registry_built")

    result = read_last_stage(tmp_path, spawn_id=spawn_id)
    assert result is None

    record(tmp_path, "upstream_catalog_or_discovery_done")
    explicit_record_path = tmp_path / f".agent/.ralph_mcp_start_stage.{spawn_id}"
    explicit_record_path.parent.mkdir(parents=True, exist_ok=True)
    explicit_record_path.write_text("config_loaded", encoding="ascii")

    assert read_last_stage(tmp_path, spawn_id=spawn_id) == "config_loaded"
    assert read_last_stage(tmp_path, spawn_id=str(uuid.uuid4())) is None


def test_stage_file_path_with_explicit_spawn_id(tmp_path: Path) -> None:
    sid = "my-test-spawn-id-42"
    p = stage_file_path(tmp_path, spawn_id=sid)
    assert sid in p.name
    assert ".ralph_mcp_start_stage.my-test-spawn-id-42" in p.name


def test_unknown_last_stage_token_is_stable() -> None:
    fallback = "last_stage_unknown"
    err = McpServerError(
        "MCP server endpoint did not become ready",
        restart_count=0,
        last_stage=fallback,
    )
    assert err.last_stage == "last_stage_unknown"


def test_mcp_server_error_last_stage_default_is_none() -> None:
    err = McpServerError("legacy", restart_count=0)
    assert err.last_stage is None


def test_mcp_server_error_last_stage_passthrough() -> None:
    err = McpServerError(
        "fd_inherit_failed",
        restart_count=0,
        last_stage="fd_inherit_failed",
    )
    assert err.last_stage == "fd_inherit_failed"


def test_mcp_server_error_elapsed_s_passthrough() -> None:
    err = McpServerError("slow", restart_count=0, elapsed_s=12.5)
    assert err.elapsed_s == 12.5
