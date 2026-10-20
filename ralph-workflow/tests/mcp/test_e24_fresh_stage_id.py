"""Pins E2.4: fresh MCP_STAGE_ID_ENV per spawn during restart / respawn.

Catalogue requirement E2.4:
- Stage-file identity reuse: env.setdefault(MCP_STAGE_ID_ENV, str(uuid.uuid4()))
  keeps an existing id. If restart_fn passes the same env mapping on every spawn,
  every candidate writes the old child's stage file, and last_stage reports the
  old child's "serving".
- Required: every spawn gets a fresh stage id. Same for any other per-spawn env value.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

# Pre-import workspace to prevent circular import between process.manager and workspace
from ralph import workspace as _workspace
from ralph.mcp.protocol.env import (
    MCP_ACCEPT_PAUSE_FILE,
    MCP_STAGE_ID_ENV,
)
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._lifecycle_deps import LifecycleDeps
from ralph.mcp.server._process_like import ProcessLike
from ralph.mcp.server._start_stage_tracker import read_last_stage
from ralph.process.manager import SpawnOptions, get_process_manager

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]
_ = _workspace


class _DummyProcess(ProcessLike):
    def __init__(self, pid: int = 1234) -> None:
        self._pid = pid
        self._poll: int | None = None

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        return self._poll

    def terminate(self, grace_period_s: float = 5.0) -> None:
        self._poll = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self._poll = -9


def test_spawn_mcp_process_assigns_fresh_stage_id_per_spawn(tmp_path: Path) -> None:
    """Every call to _spawn_mcp_process must generate a fresh UUID for MCP_STAGE_ID_ENV."""
    captured_envs: list[dict[str, str]] = []

    def fake_spawn(
        command: list[str],
        cwd: Path,
        env: dict[str, str],
        *,
        phase: str | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> ProcessLike:
        del command, cwd, phase, pass_fds
        captured_envs.append(dict(env))
        return _DummyProcess()

    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")

    stale_stage_id = "stale-pre-existing-stage-id"

    deps = LifecycleDeps(
        create_session_file=lambda _r, _s: session_file,
        # Intentionally simulate an environment mapper that returns a stale stage ID
        subprocess_env=lambda _f: {MCP_STAGE_ID_ENV: stale_stage_id},
        spawn_process=fake_spawn,
        preflight=lambda _ep, _up, _to: None,
        preflight_timeout=lambda: lifecycle.timedelta(seconds=5),
    )

    session = AgentSession(
        session_id=str(uuid.uuid4()),
        run_id="run-e24-test",
        drain="standalone",
    )

    # First spawn (e.g. initial server)
    proc1 = lifecycle._spawn_mcp_process(
        tmp_path,
        session,
        deps,
        None,
        None,
        [],
        port=9991,
        respawn=False,
        make_before_break=False,
    )

    # Second spawn (e.g. candidate replacement or restart)
    proc2 = lifecycle._spawn_mcp_process(
        tmp_path,
        session,
        deps,
        None,
        None,
        [],
        port=9992,
        respawn=True,
        make_before_break=True,
    )

    assert len(captured_envs) == 2
    env1, env2 = captured_envs[0], captured_envs[1]

    stage_id_1 = env1.get(MCP_STAGE_ID_ENV)
    stage_id_2 = env2.get(MCP_STAGE_ID_ENV)

    assert stage_id_1 is not None and len(stage_id_1) > 0
    assert stage_id_2 is not None and len(stage_id_2) > 0

    # Invariant: Neither spawn reused the stale_stage_id
    assert stage_id_1 != stale_stage_id
    assert stage_id_2 != stale_stage_id

    # Invariant: Each spawn receives a fresh, distinct stage ID
    assert stage_id_1 != stage_id_2

    # Invariant: Pause files match their respective fresh stage IDs
    assert env1.get(MCP_ACCEPT_PAUSE_FILE, "").endswith(f"accept.pause.{stage_id_1}")
    assert env2.get(MCP_ACCEPT_PAUSE_FILE, "").endswith(f"accept.pause.{stage_id_2}")

    # Invariant: StandaloneMcpProcess carries the exact stage_id
    assert proc1.stage_id == stage_id_1
    assert proc2.stage_id == stage_id_2


def test_real_children_write_distinct_stage_files_without_collision(tmp_path: Path) -> None:
    """Two real subprocesses record stages using assigned fresh stage IDs with zero collision."""
    ralph_root = Path(__file__).resolve().parents[2]
    script_path = tmp_path / "record_stage_child.py"
    script_path.write_text(
        "\n".join(
            [
                "import os, sys",
                "from pathlib import Path",
                f"sys.path.insert(0, {str(ralph_root)!r})",
                "from ralph.mcp.server._start_stage_tracker import record",
                "stage_name = sys.argv[1]",
                f"record(Path({str(tmp_path)!r}), stage_name)",
                "sys.exit(0)",
            ]
        ),
        encoding="utf-8",
    )

    stage_id_1 = str(uuid.uuid4())
    stage_id_2 = str(uuid.uuid4())

    env1 = dict(os.environ)
    env1[MCP_STAGE_ID_ENV] = stage_id_1

    env2 = dict(os.environ)
    env2[MCP_STAGE_ID_ENV] = stage_id_2

    pm = get_process_manager()

    proc1 = pm.spawn(
        [sys.executable, str(script_path), "serving"],
        SpawnOptions(cwd=str(tmp_path), env=env1, label="e24-child-1"),
    )
    proc2 = pm.spawn(
        [sys.executable, str(script_path), "config_loaded"],
        SpawnOptions(cwd=str(tmp_path), env=env2, label="e24-child-2"),
    )

    try:
        rc1 = proc1.wait(timeout=10)
        rc2 = proc2.wait(timeout=10)
    finally:
        for p in (proc1, proc2):
            if p.poll() is None:
                p.kill()
                p.wait(timeout=2)

    assert rc1 == 0
    assert rc2 == 0

    # Ensure neither process overwrote the other's stage file
    last1 = read_last_stage(tmp_path, spawn_id=stage_id_1)
    last2 = read_last_stage(tmp_path, spawn_id=stage_id_2)

    assert last1 == "serving"
    assert last2 == "config_loaded"

    # Leak check: both processes are dead
    assert proc1.poll() is not None
    assert proc2.poll() is not None
