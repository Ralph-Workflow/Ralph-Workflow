"""R4-XPROC regression: parent reads the child's stage file using the assigned spawn_id.

A real subprocess is spawned with an explicit ``RALPH_MCP_STAGE_ID``.
The child writes ONE stage (``config_loaded``) then exits before preflight.
The parent catches the resulting ``McpServerError`` and the error's
``last_stage`` field must name the stage the child actually wrote —
not ``last_stage_unknown``.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ralph.mcp.protocol.env import MCP_TEST_PRE_SERVE_GATE_ENV
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._mcp_server_extras import McpServerExtras
from ralph.mcp.server._start_stage_tracker import (
    read_last_stage,
    stage_file_path,
)
from ralph.process.manager import SpawnOptions, get_process_manager
from ralph.workspace.fs import FsWorkspace

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]


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
    script_content = "\n".join(
        [
            "import os, sys",
            "from pathlib import Path",
            f"os.environ['RALPH_MCP_STAGE_ID'] = {spawn_id!r}",
            f"sys.path.insert(0, {str(ralph_workflow_root)!r})",
            "from ralph.mcp.server._start_stage_tracker import record",
            f"record(Path({str(tmp_path)!r}), {stage_name!r})",
            "sys.exit(42)",
        ]
    )
    script_path = tmp_path / "child_record_and_exit.py"
    script_path.write_text(script_content, encoding="utf-8")

    # Run the child via ProcessManager; it exits 42 before any preflight.
    child_env = dict(os.environ)
    child_env["RALPH_MCP_STAGE_ID"] = spawn_id

    proc = get_process_manager().spawn(
        [sys.executable, str(script_path)],
        SpawnOptions(cwd=str(tmp_path), env=child_env, label="mcp-stage-attribution-child"),
    )
    try:
        # 30s covers slow CI hosts where a 10s bound on a pure-Python
        # child that writes one file can flake on xdist contention.
        returncode = proc.wait(timeout=30)
        stderr = b""
    except Exception:
        proc.kill()
        returncode = -1
        stderr = b"timeout"

    # Child exited 42 as designed.
    assert returncode == 42, f"child exited {returncode}; stderr={stderr!r}"

    # --- stage-file verification (parent reads with explicit spawn_id) --------
    last_stage = read_last_stage(tmp_path, spawn_id=spawn_id)
    assert last_stage == stage_name, (
        f"expected last_stage={stage_name!r} for spawn_id={spawn_id!r}, got {last_stage!r}"
    )

    # A wrong/mismatched spawn_id finds nothing.
    wrong_id = str(uuid.uuid4())
    assert read_last_stage(tmp_path, spawn_id=wrong_id) is None, (
        "read_last_stage should return None for a spawn_id that was not used"
    )

    # The on-disk file content matches what read_last_stage returned.
    on_disk = stage_file_path(tmp_path, spawn_id=spawn_id).read_text(encoding="ascii").strip()
    assert on_disk == stage_name


def test_mcp_server_error_last_stage_carries_child_stage(tmp_path: Path) -> None:
    """When a real child is killed during startup, McpServerError carries the child's last stage."""
    sentinel = tmp_path / "never_created_sentinel.txt"
    extras = McpServerExtras(
        extra_env={MCP_TEST_PRE_SERVE_GATE_ENV: str(sentinel)},
    )
    session = AgentSession(
        session_id="test-kill-session",
        run_id="test-run",
        drain="test",
        capabilities=set(),
    )
    workspace = FsWorkspace(tmp_path)

    spawned_procs: list[lifecycle.ProcessLike] = []
    real_deps = lifecycle._default_lifecycle_deps()

    def tracking_spawn(*args: Any, **kwargs: Any) -> lifecycle.ProcessLike:
        proc = real_deps.spawn_process(*args, **kwargs)
        spawned_procs.append(proc)
        return proc

    deps = replace(real_deps, spawn_process=tracking_spawn)

    error_box: list[McpServerError] = []
    done_event = threading.Event()

    def _start() -> None:
        try:
            lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras)
        except McpServerError as err:
            error_box.append(err)
        finally:
            done_event.set()

    worker = threading.Thread(target=_start, daemon=True)
    worker.start()

    try:
        # Wait for child to be spawned and reach registry_built stage
        deadline = time.monotonic() + 10.0
        stage_found = False
        while time.monotonic() < deadline:
            stage_files = sorted((tmp_path / ".agent").glob(".ralph_mcp_start_stage.*"))
            if stage_files:
                content = stage_files[-1].read_text(encoding="utf-8").strip()
                if content == "registry_built":
                    stage_found = True
                    break
            time.sleep(0.02)
        assert stage_found, "child never reached registry_built stage within 10s"
        assert spawned_procs, "no process was spawned"

        # Kill the child with SIGKILL while it is held at registry_built
        child_proc = spawned_procs[0]
        child_proc.kill()

        assert done_event.wait(timeout=5.0), "start_mcp_server did not raise after child kill"
        assert error_box, "no McpServerError captured"
        exc = error_box[0]

        assert exc.last_stage == "registry_built", (
            f"expected last_stage=registry_built, got {exc.last_stage!r}"
        )
        assert exc.elapsed_s is not None and exc.elapsed_s > 0, (
            f"invalid elapsed_s: {exc.elapsed_s}"
        )
        assert exc.restart_count == 0

        # Assert stage file on disk
        stage_files = sorted((tmp_path / ".agent").glob(".ralph_mcp_start_stage.*"))
        assert stage_files, "stage file missing"
        assert stage_files[-1].read_text(encoding="utf-8").strip() == "registry_built"

        # Assert child log on disk
        log_path = lifecycle.mcp_server_log_path(tmp_path)
        assert log_path.exists(), f"log path {log_path} does not exist"
        log_text = log_path.read_text(encoding="utf-8")
        assert "registry_built" in log_text, f"'registry_built' not found in log: {log_text}"
    finally:
        sentinel.touch()
        worker.join(timeout=5.0)
