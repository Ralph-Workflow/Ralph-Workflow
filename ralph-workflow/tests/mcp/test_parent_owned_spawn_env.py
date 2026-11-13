from __future__ import annotations

import contextlib
import uuid
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph import workspace as _workspace
from ralph.mcp.protocol.env import (
    MCP_ACCEPT_PAUSE_FILE,
    MCP_ACCEPT_RESUME_FILE,
    MCP_BRIDGE_PARENT_SOCK,
    MCP_SESSION_FILE_ENV,
    MCP_SNAPSHOT_DIR_ENV,
    MCP_STAGE_ID_ENV,
)
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._handover_protocol import create_handover_socketpair
from ralph.mcp.server._lifecycle_deps import LifecycleDeps
from ralph.mcp.server._process_like import ProcessLike
from ralph.mcp.upstream.tool_catalog_cache import UpstreamToolCatalogSnapshot

if TYPE_CHECKING:
    from collections.abc import Callable
    from socket import socket

    from ralph.mcp.protocol.startup import SessionLike
    from ralph.mcp.server._spawn_process import SpawnProcess
    from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess

pytestmark = pytest.mark.timeout_seconds(10)
_ = _workspace

_SESSION_ID_ENV = lifecycle.MCP_SESSION_ID_ENV

_POISON = "/poisoned/reused-extra-env"

_CALLER_EXTRAS: dict[str, str] = {
    "WORKER_ARTIFACT_DIR": "tmp/worker-7",
    "RALPH_WORKER_PHASE": "review",
}


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


def _reused_poisoned_extras() -> dict[str, str]:
    return {
        MCP_SESSION_FILE_ENV: f"{_POISON}/session.json",
        MCP_ACCEPT_RESUME_FILE: f"{_POISON}/accept.resume.dead",
        MCP_BRIDGE_PARENT_SOCK: "4242",
        MCP_STAGE_ID_ENV: f"{_POISON}/stage",
        MCP_ACCEPT_PAUSE_FILE: f"{_POISON}/accept.pause",
        _SESSION_ID_ENV: _POISON,
        MCP_SNAPSHOT_DIR_ENV: f"{_POISON}/snapshot",
        **_CALLER_EXTRAS,
    }


def _deps(
    session_file: Path,
    captured_envs: list[dict[str, str]],
    captured_pass_fds: list[tuple[int, ...]],
) -> LifecycleDeps:

    def fake_spawn(
        command: list[str],
        cwd: Path,
        env: dict[str, str],
        *,
        phase: str | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> ProcessLike:
        del command, cwd, phase
        captured_envs.append(dict(env))
        captured_pass_fds.append(pass_fds)
        return _DummyProcess()

    return LifecycleDeps(
        create_session_file=lambda _root, _session: session_file,
        subprocess_env=lambda _file: {MCP_SESSION_FILE_ENV: str(session_file)},
        spawn_process=fake_spawn,
        preflight=lambda _endpoint, _upstream, _timeout: None,
        preflight_timeout=lambda: timedelta(seconds=5),
    )


def _session() -> AgentSession:
    return AgentSession(
        session_id=str(uuid.uuid4()),
        run_id="run-e24-parent-owned",
        drain="standalone",
    )


def _spawn(
    root: Path,
    session: AgentSession,
    deps: LifecycleDeps,
    extras: dict[str, str],
    port: int,
    *,
    make_before_break: bool,
) -> StandaloneMcpProcess:
    return lifecycle._spawn_mcp_process(
        root,
        session,
        deps,
        None,
        extras,
        [],
        port=port,
        make_before_break=make_before_break,
    )


def test_cold_spawn_cannot_inherit_poisoned_accept_resume_file(tmp_path: Path) -> None:
    captured_envs: list[dict[str, str]] = []
    captured_pass_fds: list[tuple[int, ...]] = []
    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    extras = _reused_poisoned_extras()

    _spawn(
        tmp_path,
        _session(),
        _deps(session_file, captured_envs, captured_pass_fds),
        extras,
        9971,
        make_before_break=False,
    )

    assert len(captured_envs) == 1
    cold_env = captured_envs[0]
    assert cold_env.get(MCP_ACCEPT_RESUME_FILE) != f"{_POISON}/accept.resume.dead"
    assert MCP_ACCEPT_RESUME_FILE not in cold_env, (
        "cold child received RALPH_MCP_ACCEPT_RESUME_FILE "
        f"{cold_env.get(MCP_ACCEPT_RESUME_FILE)!r}; a cold spawn owns no resume "
        "sentinel, and _await_handover_resume blocks 30s on a present value"
    )
    assert captured_pass_fds[0] == ()


def test_cold_spawn_cannot_inherit_poisoned_bridge_parent_sock(tmp_path: Path) -> None:
    captured_envs: list[dict[str, str]] = []
    captured_pass_fds: list[tuple[int, ...]] = []
    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    extras = _reused_poisoned_extras()

    _spawn(
        tmp_path,
        _session(),
        _deps(session_file, captured_envs, captured_pass_fds),
        extras,
        9972,
        make_before_break=False,
    )

    assert len(captured_envs) == 1
    cold_env = captured_envs[0]
    assert cold_env.get(MCP_BRIDGE_PARENT_SOCK) != "4242"
    assert MCP_BRIDGE_PARENT_SOCK not in cold_env, (
        "cold child received RALPH_MCP_BRIDGE_PARENT_SOCK "
        f"{cold_env.get(MCP_BRIDGE_PARENT_SOCK)!r}; only a make-before-break "
        "candidate owns a handover socket"
    )


def test_cold_spawn_restores_parent_owned_session_file_path(tmp_path: Path) -> None:
    captured_envs: list[dict[str, str]] = []
    captured_pass_fds: list[tuple[int, ...]] = []
    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    extras = _reused_poisoned_extras()

    _spawn(
        tmp_path,
        _session(),
        _deps(session_file, captured_envs, captured_pass_fds),
        extras,
        9973,
        make_before_break=False,
    )

    assert len(captured_envs) == 1
    cold_env = captured_envs[0]
    assert cold_env[MCP_SESSION_FILE_ENV] != f"{_POISON}/session.json"
    assert cold_env[MCP_SESSION_FILE_ENV] == str(session_file), (
        "a reused extras mapping redirected the child's session file to "
        f"{cold_env[MCP_SESSION_FILE_ENV]!r}"
    )


def test_candidate_spawn_restores_parent_owned_session_file_path(tmp_path: Path) -> None:
    captured_envs: list[dict[str, str]] = []
    captured_pass_fds: list[tuple[int, ...]] = []
    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    extras = _reused_poisoned_extras()

    bridge = _spawn(
        tmp_path,
        _session(),
        _deps(session_file, captured_envs, captured_pass_fds),
        extras,
        9974,
        make_before_break=True,
    )
    try:
        assert len(captured_envs) == 1
        candidate_env = captured_envs[0]
        assert candidate_env[MCP_SESSION_FILE_ENV] != f"{_POISON}/session.json"
        assert candidate_env[MCP_SESSION_FILE_ENV] == str(session_file), (
            "a reused extras mapping redirected the candidate's session file to "
            f"{candidate_env[MCP_SESSION_FILE_ENV]!r}"
        )
    finally:
        bridge.shutdown()


def test_candidate_keeps_only_its_own_fresh_handover_values(tmp_path: Path) -> None:
    captured_envs: list[dict[str, str]] = []
    captured_pass_fds: list[tuple[int, ...]] = []
    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    extras = _reused_poisoned_extras()

    bridge = _spawn(
        tmp_path,
        _session(),
        _deps(session_file, captured_envs, captured_pass_fds),
        extras,
        9975,
        make_before_break=True,
    )
    try:
        assert len(captured_envs) == 1
        candidate_env = captured_envs[0]
        stage_id = candidate_env[MCP_STAGE_ID_ENV]
        assert stage_id != f"{_POISON}/stage"
        assert stage_id == bridge.stage_id

        resume = candidate_env[MCP_ACCEPT_RESUME_FILE]
        assert resume != f"{_POISON}/accept.resume.dead"
        assert resume.endswith(f"accept.resume.{stage_id}")

        pause = candidate_env[MCP_ACCEPT_PAUSE_FILE]
        assert pause != f"{_POISON}/accept.pause"
        assert pause.endswith(f"accept.pause.{stage_id}")

        parent_fd = candidate_env[MCP_BRIDGE_PARENT_SOCK]
        assert parent_fd != "4242"
        assert int(parent_fd) >= 0
        assert int(parent_fd) in captured_pass_fds[0]
    finally:
        bridge.shutdown()


def test_genuine_caller_extras_are_inherited_on_both_branches(tmp_path: Path) -> None:
    captured_envs: list[dict[str, str]] = []
    captured_pass_fds: list[tuple[int, ...]] = []
    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    extras = _reused_poisoned_extras()
    session = _session()
    deps = _deps(session_file, captured_envs, captured_pass_fds)

    cold = _spawn(tmp_path, session, deps, extras, 9976, make_before_break=False)
    candidate = _spawn(tmp_path, session, deps, extras, 9977, make_before_break=True)
    with contextlib.suppress(Exception):
        cold.shutdown()

    try:
        assert len(captured_envs) == 2
        for env in captured_envs:
            for key, value in _CALLER_EXTRAS.items():
                assert env[key] == value, f"genuine caller extra {key} was dropped"
    finally:
        candidate.shutdown()


class _InjectedFailureError(RuntimeError):
    pass


class _SessionFileFactory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.created = False

    def __call__(self, _root: Path, _session: SessionLike) -> Path:
        self.path.write_text("{}", encoding="utf-8")
        self.created = True
        return self.path


class _SpawnRecorder:
    def __init__(self, *, fail: bool = False, message: str = "injected spawn failure") -> None:
        self.calls: list[tuple[int, ...]] = []
        self._fail = fail
        self._message = message

    def __call__(
        self,
        command: list[str],
        cwd: Path,
        env: dict[str, str],
        *,
        phase: str | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> ProcessLike:
        del command, cwd, env, phase
        self.calls.append(pass_fds)
        if self._fail:
            raise _InjectedFailureError(self._message)
        return _DummyProcess()


class _SocketpairRecorder:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._real = create_handover_socketpair
        self.pairs: list[tuple[socket, socket]] = []
        self.child_fds: list[int] = []
        monkeypatch.setattr(lifecycle, "create_handover_socketpair", self)

    def __call__(self) -> tuple[socket, socket]:
        pair = self._real()
        self.pairs.append(pair)
        self.child_fds.append(pair[1].fileno())
        return pair

    @property
    def only_pair(self) -> tuple[socket, socket]:
        assert len(self.pairs) == 1, f"expected exactly one socketpair, got {len(self.pairs)}"
        return self.pairs[0]

    @staticmethod
    def assert_closed(pair: tuple[socket, socket], label: str) -> None:
        parent_sock, child_sock = pair
        assert parent_sock.fileno() == -1, f"{label}: parent socket FD still open"
        assert child_sock.fileno() == -1, f"{label}: child socket FD still open"


def _failing_subprocess_env(message: str) -> Callable[[Path], dict[str, str]]:
    def subprocess_env(session_file: Path) -> dict[str, str]:
        del session_file
        raise _InjectedFailureError(message)

    return subprocess_env


def _working_subprocess_env(session_file: Path) -> dict[str, str]:
    return {MCP_SESSION_FILE_ENV: str(session_file)}


def _injection_deps(
    *,
    create_session_file: Callable[[Path, SessionLike], Path],
    subprocess_env: Callable[[Path], dict[str, str]],
    spawn_process: SpawnProcess,
) -> LifecycleDeps:
    return LifecycleDeps(
        create_session_file=create_session_file,
        subprocess_env=subprocess_env,
        spawn_process=spawn_process,
        preflight=lambda _endpoint, _upstream, _timeout: None,
        preflight_timeout=lambda: timedelta(seconds=5),
    )


def _assert_handshake_removed(factory: _SessionFileFactory, label: str) -> None:
    assert not factory.path.exists(), (
        f"{label}: the handshake session file {factory.path} survived the failure; "
        "the outer ``except BaseException`` must unlink it on every path"
    )


def test_create_session_file_failure_propagates_before_anything_is_created(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _SocketpairRecorder(monkeypatch)
    spawn = _SpawnRecorder()
    factory = _SessionFileFactory(tmp_path / "session.json")

    def create_session_file(_root: Path, _session: SessionLike) -> Path:
        raise _InjectedFailureError("injected create_session_file failure")

    deps = _injection_deps(
        create_session_file=create_session_file,
        subprocess_env=_working_subprocess_env,
        spawn_process=spawn,
    )

    with pytest.raises(_InjectedFailureError, match="injected create_session_file failure"):
        lifecycle._spawn_mcp_process(
            tmp_path,
            _session(),
            deps,
            None,
            None,
            [],
            port=9981,
            make_before_break=True,
        )

    assert factory.created is False
    assert factory.path.exists() is False
    assert recorder.pairs == [], "no socket may be opened before the session file exists"
    assert spawn.calls == []


def test_subprocess_env_failure_unlinks_the_handshake_session_file(tmp_path: Path) -> None:
    factory = _SessionFileFactory(tmp_path / "session.json")
    spawn = _SpawnRecorder()
    deps = _injection_deps(
        create_session_file=factory,
        subprocess_env=_failing_subprocess_env("injected subprocess_env failure"),
        spawn_process=spawn,
    )

    with pytest.raises(_InjectedFailureError, match="injected subprocess_env failure"):
        lifecycle._spawn_mcp_process(tmp_path, _session(), deps, None, None, [], port=9982)

    assert factory.created is True, "the failure must land AFTER the handshake file exists"
    _assert_handshake_removed(factory, "subprocess_env failure")
    assert spawn.calls == []


def test_catalog_env_failure_unlinks_the_handshake_session_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def boom(_env: dict[str, str], _snapshot: UpstreamToolCatalogSnapshot) -> None:
        raise _InjectedFailureError("injected catalog env failure")

    monkeypatch.setattr(lifecycle, "apply_tool_catalog_env", boom)
    factory = _SessionFileFactory(tmp_path / "session.json")
    spawn = _SpawnRecorder()
    deps = _injection_deps(
        create_session_file=factory,
        subprocess_env=_working_subprocess_env,
        spawn_process=spawn,
    )

    with pytest.raises(_InjectedFailureError, match="injected catalog env failure"):
        lifecycle._spawn_mcp_process(
            tmp_path,
            _session(),
            deps,
            None,
            None,
            [],
            port=9983,
            tool_catalog_snapshot=UpstreamToolCatalogSnapshot(present=True, value={}),
        )

    assert factory.created is True
    _assert_handshake_removed(factory, "catalog env failure")
    assert spawn.calls == []


def test_socketpair_failure_unlinks_the_handshake_session_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def boom() -> tuple[socket, socket]:
        raise _InjectedFailureError("injected socketpair failure")

    monkeypatch.setattr(lifecycle, "create_handover_socketpair", boom)
    factory = _SessionFileFactory(tmp_path / "session.json")
    spawn = _SpawnRecorder()
    deps = _injection_deps(
        create_session_file=factory,
        subprocess_env=_working_subprocess_env,
        spawn_process=spawn,
    )

    with pytest.raises(_InjectedFailureError, match="injected socketpair failure"):
        lifecycle._spawn_mcp_process(
            tmp_path,
            _session(),
            deps,
            None,
            None,
            [],
            port=9984,
            make_before_break=True,
        )

    assert factory.created is True
    _assert_handshake_removed(factory, "socketpair failure")
    assert spawn.calls == []


def test_spawn_failure_closes_both_handover_sockets_and_unlinks_session_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _SocketpairRecorder(monkeypatch)
    factory = _SessionFileFactory(tmp_path / "session.json")
    spawn = _SpawnRecorder(fail=True, message="injected spawn_process failure")
    deps = _injection_deps(
        create_session_file=factory,
        subprocess_env=_working_subprocess_env,
        spawn_process=spawn,
    )

    with pytest.raises(_InjectedFailureError, match="injected spawn_process failure"):
        lifecycle._spawn_mcp_process(
            tmp_path,
            _session(),
            deps,
            None,
            None,
            [],
            port=9985,
            make_before_break=True,
        )

    assert len(spawn.calls) == 1
    assert recorder.child_fds[0] in spawn.calls[0], (
        "the child half must have been handed to the child via pass_fds"
    )
    _SocketpairRecorder.assert_closed(recorder.only_pair, "spawn_process failure")
    _assert_handshake_removed(factory, "spawn_process failure")


def test_bridge_construction_failure_closes_both_handover_sockets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def boom(**_kwargs: object) -> StandaloneMcpProcess:
        raise _InjectedFailureError("injected bridge construction failure")

    monkeypatch.setattr(lifecycle, "StandaloneMcpProcess", boom)
    recorder = _SocketpairRecorder(monkeypatch)
    factory = _SessionFileFactory(tmp_path / "session.json")
    spawn = _SpawnRecorder()
    deps = _injection_deps(
        create_session_file=factory,
        subprocess_env=_working_subprocess_env,
        spawn_process=spawn,
    )

    with pytest.raises(_InjectedFailureError, match="injected bridge construction failure"):
        lifecycle._spawn_mcp_process(
            tmp_path,
            _session(),
            deps,
            None,
            None,
            [],
            port=9986,
            make_before_break=True,
        )

    _SocketpairRecorder.assert_closed(recorder.only_pair, "bridge construction failure")
    _assert_handshake_removed(factory, "bridge construction failure")


def test_successful_candidate_keeps_parent_socket_open_and_owns_handshake_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _SocketpairRecorder(monkeypatch)
    factory = _SessionFileFactory(tmp_path / "session.json")
    spawn = _SpawnRecorder()
    deps = _injection_deps(
        create_session_file=factory,
        subprocess_env=_working_subprocess_env,
        spawn_process=spawn,
    )

    bridge = lifecycle._spawn_mcp_process(
        tmp_path,
        _session(),
        deps,
        None,
        None,
        [],
        port=9987,
        make_before_break=True,
    )
    try:
        parent_sock, child_sock = recorder.only_pair
        assert parent_sock.fileno() >= 0, "the bridge keeps the parent half open"
        assert child_sock.fileno() == -1, "the child half is released after the spawn"
        assert factory.path.exists(), "a successful spawn must not reclaim its handshake file"
    finally:
        bridge.shutdown()
