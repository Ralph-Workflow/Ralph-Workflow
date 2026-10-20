"""Pins E2.5: parent-owned snapshot / candidate survives deletion of .agent/tmp mid-run.

Catalogue requirement E2.5:
- Candidate inputs vanish or change mid-run: The agent or a cleanup phase
  (commit_cleanup, git clean, workspace resets) deletes or edits .agent/tmp, the
  session file, the tool catalog or mcp.toml.
- Required: the candidate's inputs are a snapshot owned by the parent, held
  where workspace cleanup cannot remove them, or re-materialised and checked by
  the parent before each spawn. A missing input is a typed, counted failure with
  a clear message, not a 30 s timeout.
"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

import pytest

from ralph.mcp.protocol.env import (
    MCP_SNAPSHOT_DIR_ENV,
)
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._lifecycle_deps import LifecycleDeps
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._process_like import ProcessLike
from ralph.mcp.server._snapshots import (
    read_snapshot,
    snapshot_dir,
    snapshot_inputs,
)
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.server.lifecycle import MCP_SESSION_ID_ENV
from ralph.mcp.server.runtime_session import FileBackedSession

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


class _DummyProcess(ProcessLike):
    def __init__(self, pid: int = 4321) -> None:
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


def test_candidate_survives_agent_tmp_and_workspace_cleanup(tmp_path: Path) -> None:
    """Candidate spawn survives mid-run deletion of .agent/tmp and workspace files."""
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

    session_id = f"sess-{uuid.uuid4()}"
    run_id = f"run-{uuid.uuid4()}"
    session = AgentSession(
        session_id=session_id,
        run_id=run_id,
        drain="standalone",
    )

    # Put an initial mcp.toml in workspace
    mcp_toml = tmp_path / "mcp.toml"
    mcp_toml.write_text("[mcp]\nname = 'test-mcp'\n", encoding="utf-8")

    # The canonical snapshot directory where parent stores candidate inputs
    snap_dir = snapshot_dir(tmp_path, run_id)
    snapshot_inputs(tmp_path, snap_dir)

    deps = LifecycleDeps(
        create_session_file=lambda _r, _s: snap_dir / "session.json",
        subprocess_env=lambda sf: {
            "SESSION_FILE": str(sf),
            MCP_SNAPSHOT_DIR_ENV: str(snap_dir),
        },
        spawn_process=fake_spawn,
        preflight=lambda _ep, _up, _to: None,
        preflight_timeout=lambda: lifecycle.timedelta(seconds=5),
    )

    try:
        # Initial spawn: sets up snapshot in protected location
        proc1 = lifecycle._spawn_mcp_process(
            tmp_path,
            session,
            deps,
            None,
            None,
            [],
            port=9981,
            respawn=False,
            make_before_break=False,
        )
        assert proc1 is not None

        # Verify snapshot files were written to protected dir
        assert (snap_dir / "session.json").is_file()
        assert (snap_dir / "mcp.toml").is_file()
        assert read_snapshot(snap_dir)["session_id"] == session_id

        # Simulate agent cleanup / commit cleanup wiping .agent/tmp AND workspace mcp.toml
        agent_tmp = tmp_path / ".agent" / "tmp"
        agent_tmp.mkdir(parents=True, exist_ok=True)
        (agent_tmp / "transient.data").write_text("deleted mid-run", encoding="utf-8")
        shutil.rmtree(tmp_path / ".agent")
        mcp_toml.unlink()

        assert not agent_tmp.exists()
        assert not mcp_toml.exists()

        # Protected snapshot directory remains completely intact
        assert (snap_dir / "session.json").is_file()
        assert (snap_dir / "mcp.toml").is_file()

        # Candidate spawn happens after workspace cleanup
        proc2 = lifecycle._spawn_mcp_process(
            tmp_path,
            session,
            deps,
            None,
            None,
            [],
            port=9982,
            respawn=True,
            make_before_break=True,
        )
        assert proc2 is not None

        assert len(captured_envs) == 2
        candidate_env = captured_envs[1]
        assert candidate_env[MCP_SESSION_ID_ENV] == session_id
        assert candidate_env[MCP_SNAPSHOT_DIR_ENV] == str(snap_dir)
    finally:
        # Clean up test snapshot dir
        shutil.rmtree(snap_dir, ignore_errors=True)


def test_missing_candidate_input_is_typed_counted_failure(tmp_path: Path) -> None:
    """Missing session_id raises typed McpServerError immediately, without waiting for 30s timeout."""
    invalid_session = AgentSession(
        session_id="",  # empty / missing input
        run_id="run-empty-session",
        drain="standalone",
    )

    deps = LifecycleDeps(
        create_session_file=lambda _r, _s: tmp_path / "session.json",
        subprocess_env=lambda _f: {},
        spawn_process=lambda _c, _cw, _e, **_k: _DummyProcess(),
        preflight=lambda _ep, _up, _to: None,
        preflight_timeout=lambda: lifecycle.timedelta(seconds=5),
    )

    # Invariant: Must fail immediately with McpServerError(last_stage="snapshot_missing")
    t0 = time.monotonic()
    with pytest.raises(McpServerError) as exc_info:
        lifecycle._spawn_mcp_process(
            tmp_path,
            invalid_session,
            deps,
            None,
            None,
            [],
            port=9983,
        )
    elapsed = time.monotonic() - t0

    # Bounded: Must NOT hang or wait 30 seconds
    assert elapsed < 1.0
    err = exc_info.value
    assert err.last_stage == "snapshot_missing"
    assert "session_id missing" in str(err)


def test_restart_aware_bridge_counts_failed_candidate_on_missing_input(tmp_path: Path) -> None:
    """A candidate failing due to missing input is non-terminal: counted as attempt, old child kept."""
    old_proc = _DummyProcess(pid=5001)
    old_inner = StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9984/mcp",
        process=old_proc,
        session_file=tmp_path / "old_session.json",
    )

    def failing_restart(*, make_before_break: bool = True) -> StandaloneMcpProcess:
        raise McpServerError(
            "session_id missing",
            restart_count=0,
            last_stage="snapshot_missing",
        )

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=failing_restart,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="run-missing-input-counted",
    )

    # Perform restart: candidate fails immediately due to missing input
    t0 = time.monotonic()
    with bridge._lock:
        success = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0
    assert success is False
    assert bridge.restart_count == 1
    # Old child is intact and still serving
    assert bridge.process.pid == 5001
    assert old_proc.poll() is None


def test_file_backed_session_missing_snapshot_is_typed_error(tmp_path: Path) -> None:
    """FileBackedSession raises McpServerError with last_stage='snapshot_missing' when input vanishes."""
    empty_file = tmp_path / "empty_session.json"
    empty_file.write_text("{}", encoding="utf-8")
    session = FileBackedSession(
        empty_file,
        session_id_factory=lambda: "",  # factory also returns empty
        run_id_factory=lambda: "run-id",
        workspace_root=tmp_path,
        env_getter=lambda _k: None,
    )

    with pytest.raises(McpServerError) as exc_info:
        _ = session.session_id

    assert exc_info.value.last_stage == "snapshot_missing"
