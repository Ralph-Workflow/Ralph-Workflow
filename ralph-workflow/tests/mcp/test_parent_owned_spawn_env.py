"""E2.4 (third pass): EVERY parent-owned spawn env key survives a reused extras mapping.

``tests/mcp/test_e24_parent_owned_env.py`` pins that the keys assigned on
*every* spawn (``MCP_STAGE_ID_ENV``, ``MCP_ACCEPT_PAUSE_FILE``,
``MCP_SESSION_ID_ENV``, ``MCP_SNAPSHOT_DIR_ENV``) cannot be inherited from a
reused caller mapping. Two families of parent-owned keys were still outside
that guard:

* ``MCP_SESSION_FILE_ENV`` — assigned by ``deps.subprocess_env`` BEFORE the
  ``env.update(_extra_env)`` merge (``lifecycle.py:2716`` then
  ``lifecycle.py:2724``), so a reused mapping overwrites the child's session
  file path and the child reads/writes a foreign file.
* ``MCP_ACCEPT_RESUME_FILE`` and ``MCP_BRIDGE_PARENT_SOCK`` — assigned ONLY
  inside ``if make_before_break:`` (``lifecycle.py:2747`` and
  ``lifecycle.py:2750``), so nothing clears them on a cold/initial spawn.
  Both are read by the child unconditionally: ``_await_handover_resume`` is
  called from ``FallbackStandaloneServer.run`` for EVERY spawn and returns
  early only when the value is absent
  (``_fallback_standalone_server.py:171-188``) — a stale present value makes
  the cold child wait 30 s for a resume sentinel nobody will create and then
  raise ``resume_timed_out``; ``_setup_handover_readiness`` likewise adopts
  whatever fd number a stale value names
  (``_fallback_standalone_server.py:112-120``).

This module drives the REAL production ``lifecycle._spawn_mcp_process``
preparation path and captures the exact env mapping handed to the process
launcher, with one deliberately poisoned extras mapping reused across a cold
spawn and a make-before-break candidate spawn. Only the launcher and the
``subprocess_env`` mapper are faked — the env-assembly logic under test is
production. No real process, no clock, no sleep: it stays inside the default
``make test`` profile.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

# Pre-import workspace to prevent circular import between process.manager and workspace
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
from ralph.mcp.server._lifecycle_deps import LifecycleDeps
from ralph.mcp.server._process_like import ProcessLike

if TYPE_CHECKING:
    from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess

pytestmark = pytest.mark.timeout_seconds(10)
_ = _workspace

#: Parent-owned key defined on the lifecycle module (not the protocol env map).
_SESSION_ID_ENV = lifecycle.MCP_SESSION_ID_ENV

#: Sentinel value planted in the reused extras mapping. Every assertion below
#: is written so this exact string appearing in a child env is the failure.
_POISON = "/poisoned/reused-extra-env"

#: Genuine caller extras that MUST survive the merge on every branch.
_CALLER_EXTRAS: dict[str, str] = {
    "WORKER_ARTIFACT_DIR": "tmp/worker-7",
    "RALPH_WORKER_PHASE": "review",
}


class _DummyProcess(ProcessLike):
    """A spawn double that is already terminal, so no reaping is required."""

    def __init__(self, pid: int = 4321) -> None:
        self._pid = pid
        self._poll: int | None = 0

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


def _reused_poisoned_extras() -> dict[str, str]:
    """One mapping handed to EVERY spawn — the catalogue's failure mode."""
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
    """LifecycleDeps whose only fakes are the launcher and the env mapper.

    ``subprocess_env`` mirrors the one parent-owned assignment production
    ``_subprocess_env`` makes that this test must be able to poison over
    (``lifecycle.py:2843``), without pulling in ``os.environ`` so the
    assertions cannot be satisfied by an ambient value.
    """

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
    """A cold/initial child must never see a resume sentinel, stale or fresh.

    ``_await_handover_resume`` blocks for 30 s on a PRESENT value, so a
    reused mapping that carried one would hang the cold child and then raise
    ``resume_timed_out``. The cold branch assigns no resume path, so the
    correct value is ABSENT.
    """
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
    # Corroborating half: no handover fd was reserved for this cold child.
    assert captured_pass_fds[0] == ()


def test_cold_spawn_cannot_inherit_poisoned_bridge_parent_sock(tmp_path: Path) -> None:
    """A cold/initial child must never see a handover socket fd.

    ``_setup_handover_readiness`` adopts whatever fd number the value names,
    so a stale one makes the cold child build a readiness listener on an
    unrelated (or closed) descriptor. The cold branch reserves no socket, so
    the correct value is ABSENT.
    """
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
    """The session FILE path is parent-owned on the cold branch too."""
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
    """The session FILE path is parent-owned on the candidate branch too."""
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
    """Still-true guard: the candidate's handover values are its own, not the poison.

    Green today because ``lifecycle.py:2747``/``2750`` already assign after the
    merge. It exists so the production fix for the cold branch cannot regress
    the candidate branch (e.g. a blanket ``pop`` that strips the fresh values).
    """
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
    """Still-true guard: the fix must not become a wholesale extras drop."""
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
