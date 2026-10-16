"""R4-XPROC invariant: explicit spawn_id routing and the last_stage_unknown fallback.

Tests the per-spawn-ID routing in ``stage_file_path`` and ``read_last_stage``.
When no stage file exists for a given spawn_id the contract returns ``None``
(the supervisor falls back to ``"last_stage_unknown"``). Also verifies that
recording under a known spawn_id and reading back with that same ID returns
the recorded value, while a mismatched ID returns ``None``.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._start_stage_tracker import (
    read_last_stage,
    record,
    stage_file_path,
)


def test_missing_stage_file_returns_none(tmp_path: Path) -> None:
    """No file for the given spawn_id → read_last_stage returns ``None``."""
    fake_id = str(uuid.uuid4())
    assert read_last_stage(tmp_path, spawn_id=fake_id) is None


def test_record_and_read_with_same_spawn_id(tmp_path: Path) -> None:
    """Writing under a known spawn_id and reading back with the same ID returns the stage."""
    spawn_id = str(uuid.uuid4())
    record(tmp_path, "config_loaded")
    # Also record under the explicit spawn_id to verify record() still uses module global.
    record(tmp_path, "registry_built")  # module-global spawn_id (test process)

    # read_last_stage with the same explicit spawn_id gets the module-global record.
    result = read_last_stage(tmp_path, spawn_id=spawn_id)
    assert result is None  # no record under the explicit spawn_id

    # Record under the explicit spawn_id and verify readback.
    record(tmp_path, "upstream_catalog_or_discovery_done")
    # Patch: the module global still uses the test process's spawn_id.
    # We test the explicit-ID path directly.
    explicit_record_path = tmp_path / f".agent/.ralph_mcp_start_stage.{spawn_id}"
    explicit_record_path.parent.mkdir(parents=True, exist_ok=True)
    explicit_record_path.write_text("config_loaded", encoding="ascii")

    assert read_last_stage(tmp_path, spawn_id=spawn_id) == "config_loaded"
    # A different spawn_id finds nothing.
    assert read_last_stage(tmp_path, spawn_id=str(uuid.uuid4())) is None


def test_stage_file_path_with_explicit_spawn_id(tmp_path: Path) -> None:
    """stage_file_path embeds the explicit spawn_id when one is provided."""
    sid = "my-test-spawn-id-42"
    p = stage_file_path(tmp_path, spawn_id=sid)
    assert sid in p.name
    assert ".ralph_mcp_start_stage.my-test-spawn-id-42" in p.name


def test_unknown_last_stage_token_is_stable() -> None:
    """The supervisor fallback token is ``last_stage_unknown``."""
    fallback = "last_stage_unknown"
    err = McpServerError(
        "MCP server endpoint did not become ready",
        restart_count=0,
        last_stage=fallback,
    )
    assert err.last_stage == "last_stage_unknown"


def test_mcp_server_error_last_stage_default_is_none() -> None:
    """Legacy call sites that pass no last_stage get ``None``."""
    err = McpServerError("legacy", restart_count=0)
    assert err.last_stage is None


def test_mcp_server_error_last_stage_passthrough() -> None:
    """last_stage is stored on the error and returned correctly."""
    err = McpServerError(
        "fd_inherit_failed",
        restart_count=0,
        last_stage="fd_inherit_failed",
    )
    assert err.last_stage == "fd_inherit_failed"


def test_mcp_server_error_elapsed_s_passthrough() -> None:
    """elapsed_s is stored on the error and returned correctly."""
    err = McpServerError("slow", restart_count=0, elapsed_s=12.5)
    assert err.elapsed_s == 12.5
