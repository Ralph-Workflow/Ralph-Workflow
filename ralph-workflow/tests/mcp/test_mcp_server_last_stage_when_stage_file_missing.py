"""R4 invariant: when the stage file is absent, the error names ``last_stage_unknown``."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.subprocess_e2e


import tempfile
from pathlib import Path

from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._start_stage_tracker import read_last_stage


def test_missing_stage_file_returns_none() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        assert read_last_stage(Path(tmp)) is None


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
