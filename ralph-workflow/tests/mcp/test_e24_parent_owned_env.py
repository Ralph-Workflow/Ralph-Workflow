from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from ralph import workspace as _workspace
from ralph.mcp.protocol.env import (
    MCP_ACCEPT_PAUSE_FILE,
    MCP_SNAPSHOT_DIR_ENV,
    MCP_STAGE_ID_ENV,
)
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._lifecycle_deps import LifecycleDeps
from ralph.mcp.server._process_like import ProcessLike

pytestmark = pytest.mark.timeout_seconds(10)
_ = _workspace

_SESSION_ID_ENV = lifecycle.MCP_SESSION_ID_ENV


class _DummyProcess(ProcessLike):
    def __init__(self, pid: int = 4321) -> None:
        self._pid = pid
        self._poll: int | None = 0

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        return self._poll

    def terminate(self, grace_period_s: float = 5.0, **_kwargs: object) -> None:
        self._poll = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self, **_kwargs: object) -> None:
        self._poll = -9


def _deps(
    session_file: Path,
    captured_envs: list[dict[str, str]],
) -> LifecycleDeps:
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

    return LifecycleDeps(
        create_session_file=lambda _root, _session: session_file,
        subprocess_env=lambda _file: {},
        spawn_process=fake_spawn,
        preflight=lambda _endpoint, _upstream, _timeout: None,
        preflight_timeout=lambda: timedelta(seconds=5),
    )


def test_e24_reused_extra_env_cannot_overwrite_parent_owned_spawn_identity(tmp_path: Path) -> None:
    captured_envs: list[dict[str, str]] = []
    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    deps = _deps(session_file, captured_envs)

    session_id_value = str(uuid.uuid4())
    session = AgentSession(session_id=session_id_value, run_id="run-e24-extras", drain="standalone")

    poisoned = "reused-poison-value"
    reused_extra_env = {
        MCP_STAGE_ID_ENV: poisoned,
        MCP_ACCEPT_PAUSE_FILE: poisoned,
        _SESSION_ID_ENV: poisoned,
        MCP_SNAPSHOT_DIR_ENV: poisoned,
        "WORKER_ARTIFACT_DIR": "tmp/worker-7",
    }

    first = lifecycle._spawn_mcp_process(
        tmp_path,
        session,
        deps,
        None,
        reused_extra_env,
        [],
        port=9971,
        respawn=False,
        make_before_break=False,
    )
    second = lifecycle._spawn_mcp_process(
        tmp_path,
        session,
        deps,
        None,
        reused_extra_env,
        [],
        port=9972,
        respawn=True,
        make_before_break=False,
    )

    assert len(captured_envs) == 2
    for env in captured_envs:
        assert env[MCP_STAGE_ID_ENV] != poisoned
        assert env[MCP_ACCEPT_PAUSE_FILE] != poisoned
        assert env[_SESSION_ID_ENV] == session_id_value
        assert env[MCP_SNAPSHOT_DIR_ENV] != poisoned
        assert env["WORKER_ARTIFACT_DIR"] == "tmp/worker-7"

    assert captured_envs[0][MCP_STAGE_ID_ENV] != captured_envs[1][MCP_STAGE_ID_ENV]
    assert captured_envs[0][MCP_ACCEPT_PAUSE_FILE].endswith(
        f"accept.pause.{captured_envs[0][MCP_STAGE_ID_ENV]}"
    )
    assert captured_envs[1][MCP_ACCEPT_PAUSE_FILE].endswith(
        f"accept.pause.{captured_envs[1][MCP_STAGE_ID_ENV]}"
    )
    assert first.stage_id == captured_envs[0][MCP_STAGE_ID_ENV]
    assert second.stage_id == captured_envs[1][MCP_STAGE_ID_ENV]
