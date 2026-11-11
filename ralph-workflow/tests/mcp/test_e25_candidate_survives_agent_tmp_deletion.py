from __future__ import annotations

import contextlib
import json
import shutil
import sys
import time
import uuid
from collections.abc import Callable, Iterator
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
from ralph.process.manager import ManagedProcess, SpawnOptions, get_process_manager

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


@pytest.fixture
def managed_spawner(tmp_path: Path) -> Iterator[Callable[..., ManagedProcess]]:
    spawned: list[ManagedProcess] = []
    pm = get_process_manager()

    def _spawn(
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        pass_fds: tuple[int, ...] = (),
        label: str = "e25-test-child",
        stdout: int | None = None,
        stderr: int | None = None,
    ) -> ManagedProcess:
        proc = pm.spawn(
            args,
            SpawnOptions(
                cwd=str(tmp_path),
                env=env,
                pass_fds=pass_fds,
                label=label,
                stdout=stdout,
                stderr=stderr,
            ),
        )
        spawned.append(proc)
        return proc

    try:
        yield _spawn
    finally:
        for p in spawned:
            if p.poll() is None:
                with contextlib.suppress(Exception):
                    p.terminate(grace_period_s=1.0)
                with contextlib.suppress(Exception):
                    p.wait(timeout=2.0)


def _e25_probe_script_template(out_path: str) -> str:
    return (
        "import json\n"
        "import os\n"
        "import sys\n"
        "_all = {k: v for k, v in os.environ.items() "
        "if k.startswith('MCP_') or k.startswith('RALPH_MCP_') or k == 'SESSION_FILE'}\n"
        "_all['__child_pid__'] = str(os.getpid())\n"
        f"_out_path = {out_path!r}\n"
        "with open(_out_path, 'w', encoding='utf-8') as fh:\n"
        "    json.dump(_all, fh, sort_keys=True)\n"
        "sys.exit(0)\n"
    )


def _e25_simulate_workspace_cleanup(
    tmp_path: Path,
    mcp_toml: Path,
) -> None:
    agent_tmp = tmp_path / ".agent" / "tmp"
    agent_tmp.mkdir(parents=True, exist_ok=True)
    (agent_tmp / "transient.data").write_text("deleted mid-run", encoding="utf-8")
    shutil.rmtree(tmp_path / ".agent")
    mcp_toml.unlink()


def _e25_assert_snapshot_intact(snap_dir: Path, session_id: str) -> None:
    assert (snap_dir / "session.json").is_file()
    assert (snap_dir / "mcp.toml").is_file()
    assert read_snapshot(snap_dir)["session_id"] == session_id


def _e25_assert_probe_ran(
    real_procs: list[ManagedProcess],
    *,
    index: int,
    probe_path: Path,
) -> ManagedProcess:
    assert len(real_procs) > index, f"real_spawn was not invoked for child #{index + 1}"
    proc = real_procs[index]
    proc.wait(timeout=3.0)
    assert proc.poll() == 0, f"probe child #{index + 1} did not exit cleanly (poll={proc.poll()})"
    assert probe_path.is_file(), (
        f"probe child #{index + 1} did not write env sentinel at {probe_path}"
    )
    return proc


def test_candidate_survives_agent_tmp_and_workspace_cleanup(
    managed_spawner: Callable[..., ManagedProcess],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snap_root = tmp_path / "snapshots-override"
    monkeypatch.setenv("RALPH_MCP_SNAPSHOT_DIR", str(snap_root))

    session_id = f"sess-{uuid.uuid4()}"
    run_id = f"run-{uuid.uuid4()}"

    mcp_toml = tmp_path / "mcp.toml"
    mcp_toml.write_text("[mcp]\nname = 'test-mcp'\n", encoding="utf-8")
    snap_dir = snapshot_dir(tmp_path, run_id)
    snapshot_inputs(tmp_path, snap_dir)

    probe1_path = snap_dir / "probe_child1_env.json"
    probe2_path = snap_dir / "probe_child2_env.json"

    captured_envs: list[dict[str, str]] = []
    real_procs: list[ManagedProcess] = []
    probe_scripts: list[Path] = []

    def _write_probe(sentinel: Path) -> Path:
        script = tmp_path / f"probe_{sentinel.stem}.py"
        script.write_text(_e25_probe_script_template(str(sentinel)), encoding="utf-8")
        return script

    for sentinel in (probe1_path, probe2_path):
        probe_scripts.append(_write_probe(sentinel))

    def real_spawn(
        command: list[str],
        cwd: Path,
        env: dict[str, str],
        *,
        phase: str | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> ProcessLike:
        del command, cwd, phase, pass_fds
        captured_envs.append(dict(env))
        probe_script = probe_scripts[len(real_procs)]
        proc = managed_spawner(
            [sys.executable, str(probe_script)],
            env=dict(env),
            label=f"e25-cand-{len(real_procs) + 1}",
        )
        real_procs.append(proc)
        return proc

    session = AgentSession(session_id=session_id, run_id=run_id, drain="standalone")
    deps = LifecycleDeps(
        create_session_file=lambda _r, _s: snap_dir / "session.json",
        subprocess_env=lambda sf: {
            "SESSION_FILE": str(sf),
            MCP_SNAPSHOT_DIR_ENV: str(snap_dir),
        },
        spawn_process=real_spawn,
        preflight=lambda _ep, _up, _to: None,
        preflight_timeout=lambda: lifecycle.timedelta(seconds=5),
    )

    try:
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

        assert (snap_dir / "session.json").is_file(), (
            f"_spawn_mcp_process did not write session.json to {snap_dir}"
        )
        assert (snap_dir / "mcp.toml").is_file(), (
            "_spawn_mcp_process did not write mcp.toml to snap_dir"
        )
        _e25_assert_probe_ran(real_procs, index=0, probe_path=probe1_path)

        _e25_simulate_workspace_cleanup(tmp_path, mcp_toml)
        assert not (tmp_path / ".agent").exists()
        assert not mcp_toml.exists()
        _e25_assert_snapshot_intact(snap_dir, session_id)

        proc2 = lifecycle._spawn_mcp_process(
            tmp_path,
            session,
            deps,
            None,
            None,
            [],
            port=9982,
            respawn=True,
            make_before_break=False,
        )
        assert proc2 is not None

        assert len(captured_envs) == 2
        candidate_env = captured_envs[1]
        assert candidate_env[MCP_SESSION_ID_ENV] == session_id
        assert candidate_env[MCP_SNAPSHOT_DIR_ENV] == str(snap_dir)

        second = _e25_assert_probe_ran(real_procs, index=1, probe_path=probe2_path)

        sentinel = json.loads(probe2_path.read_text(encoding="utf-8"))
        assert sentinel.get(MCP_SESSION_ID_ENV) == session_id
        assert sentinel.get(MCP_SNAPSHOT_DIR_ENV) == str(snap_dir)
        assert int(sentinel["__child_pid__"]) == second.pid

        _e25_assert_snapshot_intact(snap_dir, session_id)
    finally:
        for proc in real_procs:
            if proc.poll() is None:
                with contextlib.suppress(Exception):
                    proc.terminate(grace_period_s=1.0)
                with contextlib.suppress(Exception):
                    proc.wait(timeout=2.0)
        shutil.rmtree(snap_dir, ignore_errors=True)


def test_missing_candidate_input_is_typed_counted_failure(tmp_path: Path) -> None:
    invalid_session = AgentSession(
        session_id="",
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

    assert elapsed < 1.0
    err = exc_info.value
    assert err.last_stage == "snapshot_missing"
    assert "session_id missing" in str(err)


def test_restart_aware_bridge_counts_failed_candidate_on_missing_input(tmp_path: Path) -> None:
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

    t0 = time.monotonic()
    with bridge._lock:
        success = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0
    assert success is False
    assert bridge.restart_count == 1
    assert bridge.process.pid == 5001
    assert old_proc.poll() is None


def test_file_backed_session_missing_snapshot_is_typed_error(tmp_path: Path) -> None:
    empty_file = tmp_path / "empty_session.json"
    empty_file.write_text("{}", encoding="utf-8")
    session = FileBackedSession(
        empty_file,
        session_id_factory=lambda: "",
        run_id_factory=lambda: "run-id",
        workspace_root=tmp_path,
        env_getter=lambda _k: None,
    )

    with pytest.raises(McpServerError) as exc_info:
        _ = session.session_id

    assert exc_info.value.last_stage == "snapshot_missing"
