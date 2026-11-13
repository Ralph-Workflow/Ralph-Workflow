from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path

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
from ralph.process.manager import ProcessTerminationIssuer, SpawnOptions, get_process_manager
from ralph.workspace.fs import FsWorkspace

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]


def test_real_child_stage_file_attributed_via_explicit_spawn_id(
    tmp_path: Path,
) -> None:
    spawn_id = str(uuid.uuid4())
    stage_name = "config_loaded"

    ralph_workflow_root = Path(__file__).resolve().parents[2]

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

    child_env = dict(os.environ)
    child_env["RALPH_MCP_STAGE_ID"] = spawn_id

    proc = get_process_manager().spawn(
        [sys.executable, str(script_path)],
        SpawnOptions(cwd=str(tmp_path), env=child_env, label="mcp-stage-attribution-child"),
    )
    try:
        returncode = proc.wait(timeout=30)
        stderr = b""
    except Exception:
        proc.kill(issuer=ProcessTerminationIssuer.OPERATOR)
        returncode = -1
        stderr = b"timeout"

    assert returncode == 42, f"child exited {returncode}; stderr={stderr!r}"

    last_stage = read_last_stage(tmp_path, spawn_id=spawn_id)
    assert last_stage == stage_name, (
        f"expected last_stage={stage_name!r} for spawn_id={spawn_id!r}, got {last_stage!r}"
    )

    wrong_id = str(uuid.uuid4())
    assert read_last_stage(tmp_path, spawn_id=wrong_id) is None, (
        "read_last_stage should return None for a spawn_id that was not used"
    )

    on_disk = stage_file_path(tmp_path, spawn_id=spawn_id).read_text(encoding="ascii").strip()
    assert on_disk == stage_name


def test_mcp_server_error_last_stage_carries_child_stage(tmp_path: Path) -> None:
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

    def tracking_spawn(
        command: list[str],
        cwd: Path,
        env: dict[str, str],
        *,
        phase: str | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> lifecycle.ProcessLike:
        proc = real_deps.spawn_process(
            command,
            cwd,
            env,
            phase=phase,
            pass_fds=pass_fds,
        )
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

        child_proc = spawned_procs[0]
        child_proc.kill(issuer=ProcessTerminationIssuer.OPERATOR)

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

        stage_files = sorted((tmp_path / ".agent").glob(".ralph_mcp_start_stage.*"))
        assert stage_files, "stage file missing"
        assert stage_files[-1].read_text(encoding="utf-8").strip() == "registry_built"

        log_path = lifecycle.mcp_server_log_path(tmp_path)
        assert log_path.exists(), f"log path {log_path} does not exist"
        log_text = log_path.read_text(encoding="utf-8")
        assert "registry_built" in log_text, f"'registry_built' not found in log: {log_text}"
    finally:
        sentinel.touch()
        worker.join(timeout=5.0)
