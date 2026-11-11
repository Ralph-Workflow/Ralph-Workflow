from __future__ import annotations

import os
import socket
import uuid
from pathlib import Path

import pytest

from ralph import workspace as _workspace
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._lifecycle_deps import LifecycleDeps
from ralph.mcp.server._process_like import ProcessLike

_ = _workspace


pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(15)]


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


def _make_session() -> AgentSession:
    return AgentSession(
        session_id=str(uuid.uuid4()),
        run_id="run-x",
        drain="standalone",
    )


def test_create_session_file_failure_raises_typed_error_with_no_unboundlocal(
    tmp_path: Path,
) -> None:

    class _SessionFileError(RuntimeError):
        pass

    def failing_create_session_file(_root: Path, _session: AgentSession) -> Path:
        raise _SessionFileError("synthetic create_session_file failure")

    deps = LifecycleDeps(
        create_session_file=failing_create_session_file,
        subprocess_env=lambda _f: {"PATH": os.environ.get("PATH", "")},
        spawn_process=lambda *_a, **_k: _DummyProcess(),
        preflight=lambda _ep, _up, _to: None,
        preflight_timeout=lambda: lifecycle.timedelta(seconds=5),
    )

    session = _make_session()

    with pytest.raises(_SessionFileError) as excinfo:
        lifecycle._spawn_mcp_process(
            root=tmp_path,
            session=session,
            deps=deps,
            phase=None,
            _extra_env=None,
            _visible_tools=[],
            port=0,
            inherited_fd=None,
            respawn=False,
            make_before_break=False,
        )

    assert "_SessionFileError" in type(excinfo.value).__name__
    assert "UnboundLocalError" not in type(excinfo.value).__name__


def test_subprocess_env_failure_raises_typed_error_with_no_unboundlocal(
    tmp_path: Path,
) -> None:

    class _EnvMapperFailureError(RuntimeError):
        pass

    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")

    def failing_subprocess_env(_file: Path) -> dict[str, str]:
        raise _EnvMapperFailureError("synthetic subprocess_env failure")

    deps = LifecycleDeps(
        create_session_file=lambda _r, _s: session_file,
        subprocess_env=failing_subprocess_env,
        spawn_process=lambda *_a, **_k: _DummyProcess(),
        preflight=lambda _ep, _up, _to: None,
        preflight_timeout=lambda: lifecycle.timedelta(seconds=5),
    )

    session = _make_session()

    with pytest.raises(_EnvMapperFailureError) as excinfo:
        lifecycle._spawn_mcp_process(
            root=tmp_path,
            session=session,
            deps=deps,
            phase=None,
            _extra_env=None,
            _visible_tools=[],
            port=0,
            inherited_fd=None,
            respawn=False,
            make_before_break=False,
        )

    assert "_EnvMapperFailureError" in type(excinfo.value).__name__
    assert not session_file.exists(), (
        "session file at "
        f"{session_file} "
        "was not removed after the subprocess_env fault; the caller must "
        "unlink it in its except handler before re-raising."
    )


def test_post_socketpair_in_helper_failure_closes_both_socket_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    class _PostSocketpairFaultError(RuntimeError):
        pass

    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")

    fileno_call_count = {"n": 0}
    real_fileno = socket.socket.fileno
    created_sockets: list[socket.socket] = []
    real_create_socketpair = lifecycle.create_handover_socketpair

    def capturing_socketpair() -> tuple[socket.socket, socket.socket]:
        sockets = real_create_socketpair()
        created_sockets.extend(sockets)
        return sockets

    def counting_fileno(self: socket.socket) -> int:
        fileno_call_count["n"] += 1
        if fileno_call_count["n"] == 1:
            raise _PostSocketpairFaultError(
                "synthetic post-socketpair fault (fileno inside helper)"
            )
        return real_fileno(self)

    monkeypatch.setattr(socket.socket, "fileno", counting_fileno)
    monkeypatch.setattr(lifecycle, "create_handover_socketpair", capturing_socketpair)

    deps = LifecycleDeps(
        create_session_file=lambda _r, _s: session_file,
        subprocess_env=lambda _f: {"PATH": os.environ.get("PATH", "")},
        spawn_process=lambda *_a, **_k: _DummyProcess(),
        preflight=lambda _ep, _up, _to: None,
        preflight_timeout=lambda: lifecycle.timedelta(seconds=5),
    )

    session = _make_session()

    with pytest.raises(_PostSocketpairFaultError) as excinfo:
        lifecycle._spawn_mcp_process(
            root=tmp_path,
            session=session,
            deps=deps,
            phase=None,
            _extra_env=None,
            _visible_tools=[],
            port=0,
            inherited_fd=None,
            respawn=False,
            make_before_break=True,
        )

    assert "_PostSocketpairFaultError" in type(excinfo.value).__name__
    assert "UnboundLocalError" not in type(excinfo.value).__name__
    assert not session_file.exists(), (
        "session file at "
        f"{session_file} "
        "was not removed after the post-socketpair fault; the caller must "
        "unlink it in its except handler before re-raising."
    )
    assert len(created_sockets) == 2
    assert all(real_fileno(sock) == -1 for sock in created_sockets)
