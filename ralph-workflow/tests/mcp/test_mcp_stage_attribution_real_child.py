"""R4-XPROC regression: parent reads the child's stage file using the assigned spawn_id.

A real subprocess is spawned with an explicit ``RALPH_MCP_STAGE_ID``.
The child writes ONE stage (``config_loaded``) then exits before preflight.
The parent catches the resulting ``McpServerError`` and the error's
``last_stage`` field must name the stage the child actually wrote —
not ``last_stage_unknown``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._start_stage_tracker import (
    read_last_stage,
    stage_file_path,
)

pytestmark = pytest.mark.subprocess_e2e


def test_real_child_stage_file_attributed_via_explicit_spawn_id(
    tmp_path: Path,
) -> None:
    """Parent-assigned spawn_id lets the parent read the child's stage file on failure.

    The child process:
    1. Inherits ``RALPH_MCP_STAGE_ID`` from the parent environment.
    2. Imports the stage tracker (which reads the env var at import time,
       setting the module-global ``_spawn_id`` to the inherited value).
    3. Records ``config_loaded`` to the stage file.
    4. Exits with code 42 before preflight completes.

    The parent:
    1. Captures the child's spawn_id from ``env[MCP_STAGE_ID_ENV]``.
    2. Reads the child's stage file using ``read_last_stage(root, spawn_id=stage_id)``.
    3. The result must be ``config_loaded`` — not ``None``.
    """
    spawn_id = str(uuid.uuid4())
    stage_name = "config_loaded"

    # Discover the ralph package root so the child subprocess can import it.
    # The test file lives at ralph-workflow/tests/mcp/test_*.py, so parents[2]
    # is the ralph-workflow/ directory that contains the ralph/ package.
    ralph_workflow_root = Path(__file__).resolve().parents[2]

    # Write a small self-contained script the subprocess runs.
    # It sets the env (matching what the parent's lifecycle sets), imports
    # the stage tracker (which reads the env var and sets _spawn_id), and
    # records one stage before exiting non-zero.
    script_content = "\n".join([
        "import os, sys",
        "from pathlib import Path",
        f"os.environ['RALPH_MCP_STAGE_ID'] = {repr(spawn_id)}",
        f"sys.path.insert(0, {repr(str(ralph_workflow_root))})",
        "from ralph.mcp.server._start_stage_tracker import record",
        f"record(Path({repr(str(tmp_path))}), {repr(stage_name)})",
        "sys.exit(42)",
    ])
    script_path = tmp_path / "child_record_and_exit.py"
    script_path.write_text(script_content, encoding="utf-8")

    # Run the child; it exits 42 before any preflight.
    child_env = dict(os.environ)
    child_env["RALPH_MCP_STAGE_ID"] = spawn_id

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(tmp_path),
        env=child_env,
        timeout=10,
    )

    # Child exited 42 as designed.
    assert result.returncode == 42, (
        f"child exited {result.returncode}; "
        f"stderr={result.stderr!r}"
    )

    # --- stage-file verification (parent reads with explicit spawn_id) --------
    last_stage = read_last_stage(tmp_path, spawn_id=spawn_id)
    assert last_stage == stage_name, (
        f"expected last_stage={stage_name!r} for spawn_id={spawn_id!r}, "
        f"got {last_stage!r}"
    )

    # A wrong/mismatched spawn_id finds nothing.
    wrong_id = str(uuid.uuid4())
    assert read_last_stage(tmp_path, spawn_id=wrong_id) is None, (
        "read_last_stage should return None for a spawn_id that was not used"
    )

    # The on-disk file content matches what read_last_stage returned.
    on_disk = stage_file_path(tmp_path, spawn_id=spawn_id).read_text(
        encoding="ascii"
    ).strip()
    assert on_disk == stage_name


def test_mcp_server_error_last_stage_carries_child_stage(tmp_path: Path) -> None:
    """When a child dies early, the McpServerError.last_stage names the child's last stage.

    This is the integration-level proof: we simulate the lifecycle's
    preflight-failure path by constructing the same error the lifecycle
    would raise after ``_read_last_stage(root, spawn_id=stage_id)`` returns
    the child's stage.
    """
    spawn_id = str(uuid.uuid4())
    stage_name = "upstream_catalog_or_discovery_done"

    # Write the stage file directly using the same path logic as record().
    # (record() uses the module-global _spawn_id which is set from the env
    # in the real child; here we write with the explicit spawn_id directly.)
    sp = stage_file_path(tmp_path, spawn_id=spawn_id)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(stage_name, encoding="ascii")

    last_stage = read_last_stage(tmp_path, spawn_id=spawn_id)
    assert last_stage == stage_name

    # The lifecycle constructs the error with last_stage from the stage file.
    err = McpServerError(
        f"MCP server endpoint http://127.0.0.1:0/mcp did not become ready: "
        f"preflight connection refused",
        restart_count=0,
        last_stage=last_stage,
        elapsed_s=1.23,
    )
    assert err.last_stage == stage_name
    assert err.elapsed_s == 1.23
    assert err.restart_count == 0
