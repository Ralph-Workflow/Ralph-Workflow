"""Pins the early-exception boundary contract for ``_spawn_mcp_process``.

When the spawn pipeline fails BEFORE ``deps.spawn_process`` runs (a session-file
creation failure, a ``subprocess_env`` mapper failure, or a fault injected
immediately after ``_spawn_mcp_process`` itself creates the handover
socketpair), the original typed error must propagate — never an
``UnboundLocalError`` from a local that was only bound inside the failed
``try`` block. The session file and socket handles must also be cleaned up so
no file descriptor is leaked across the boundary.

Every resource the caller owns is bound to ``None`` in the OUTER frame before
the first fallible call, so the single ``except BaseException`` at the end of
the ``try`` is the only cleanup point: it closes both socket halves and
unlinks the handshake file. The env-assembly helper ``_build_spawn_env`` is a
pure computation — it owns no resource and never appears on this boundary.

Behavioural matrix (each case must pass on the post-fix tree):

- ``create_session_file`` failure -> original typed error surfaces, the session
  file path is unlinked, open-fd count is unchanged.
- ``subprocess_env`` failure -> original typed error surfaces, open-fd count is
  unchanged (the session file may or may not yet exist when the helper runs).
- Post-socketpair fault. The injection sits on the ``child_sock.fileno()``
  call that ``_spawn_mcp_process`` issues the instant
  ``create_handover_socketpair`` returns a pair, so the spawn path still owns
  both file descriptors when the fault fires. This proves the cleanup contract
  (PA-002/PA-003): BOTH socket ends must be closed before the typed error
  propagates, and the caller never observes a leaked fd. Every case drives
  the public ``_spawn_mcp_process`` surface, never the inner
  ``_build_spawn_env`` env-assembly helper, so the assertions pin the boundary
  that callers actually observe.
"""

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


def _count_open_fds() -> int:
    """Count the test process's open file descriptors (best-effort)."""
    pid_fd_dir = Path(f"/proc/{os.getpid()}/fd")
    try:
        return sum(1 for _ in pid_fd_dir.iterdir())
    except OSError:
        try:
            return sum(1 for _ in Path("/dev/fd").iterdir())
        except OSError:
            return -1


def _make_session() -> AgentSession:
    return AgentSession(
        session_id=str(uuid.uuid4()),
        run_id="run-x",
        drain="standalone",
    )


def test_create_session_file_failure_raises_typed_error_with_no_unboundlocal(
    tmp_path: Path,
) -> None:
    """(a) ``create_session_file`` failure: typed error propagates, no leak.

    When the seam raises BEFORE returning a path, the caller's ``session_file``
    local stays at its pre-initialized ``None`` value, so the except handler's
    ``if session_file is not None: ...`` guard correctly skips the ``unlink``
    call. The seam's own partial writes (if any) are the seam's
    responsibility — the contract being pinned here is the TYpeD error
    propagation and the absence of an ``UnboundLocalError`` masked traceback.
    """

    class _SessionFileError(RuntimeError):
        pass

    # No real session file is created; the seam raises before returning.
    fds_before = _count_open_fds()

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

    # No UnboundLocalError masked the typed error.
    assert "_SessionFileError" in type(excinfo.value).__name__
    assert "UnboundLocalError" not in type(excinfo.value).__name__
    # Open-fd count is unchanged.
    if fds_before >= 0:
        assert _count_open_fds() == fds_before


def test_subprocess_env_failure_raises_typed_error_with_no_unboundlocal(
    tmp_path: Path,
) -> None:
    """(b) ``subprocess_env`` failure: typed error propagates, no leak."""

    class _EnvMapperFailureError(RuntimeError):
        pass

    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")

    fds_before = _count_open_fds()

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
    # The caller's except handler must unlink the session file on
    # ``subprocess_env`` failure so the temporary file does not survive
    # across the boundary.
    assert not session_file.exists(), (
        "session file at "
        f"{session_file} "
        "was not removed after the subprocess_env fault; the caller must "
        "unlink it in its except handler before re-raising."
    )
    if fds_before >= 0:
        assert _count_open_fds() == fds_before


def test_post_socketpair_in_helper_failure_closes_both_socket_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(c) Fault after the socketpair: BOTH socket ends are closed.

    The injection sits on ``socket.socket.fileno``. The test drives the public
    ``_spawn_mcp_process`` surface, not the inner ``_build_spawn_env``
    env-assembly helper: ``make_before_break=True`` makes the caller call
    ``create_handover_socketpair`` (which returns a real pair) and then
    immediately call ``child_sock.fileno()`` to compute the fd number it will
    hand to the child via env. By wrapping ``socket.socket.fileno`` to raise ON
    THE FIRST CALL ONLY, we inject a fault AFTER the socketpair exists but
    BEFORE the spawn completes. The spawn path must then close BOTH ends and
    re-raise the typed error.
    """

    class _PostSocketpairFaultError(RuntimeError):
        pass

    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")

    # Wrap ``socket.socket.fileno`` so the FIRST call raises the typed fault.
    # Any subsequent call (e.g. from cleanup logic, psutil probing) goes through
    # the real implementation.
    fileno_call_count = {"n": 0}
    real_fileno = socket.socket.fileno

    def counting_fileno(self: socket.socket) -> int:
        fileno_call_count["n"] += 1
        if fileno_call_count["n"] == 1:
            raise _PostSocketpairFaultError(
                "synthetic post-socketpair fault (fileno inside helper)"
            )
        return real_fileno(self)

    monkeypatch.setattr(socket.socket, "fileno", counting_fileno)

    deps = LifecycleDeps(
        create_session_file=lambda _r, _s: session_file,
        subprocess_env=lambda _f: {"PATH": os.environ.get("PATH", "")},
        spawn_process=lambda *_a, **_k: _DummyProcess(),
        preflight=lambda _ep, _up, _to: None,
        preflight_timeout=lambda: lifecycle.timedelta(seconds=5),
    )

    session = _make_session()

    fds_before = _count_open_fds()

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
            make_before_break=True,  # Required to drive the socketpair branch
        )

    # Typed error, not UnboundLocalError.
    assert "_PostSocketpairFaultError" in type(excinfo.value).__name__
    assert "UnboundLocalError" not in type(excinfo.value).__name__
    # The caller's single ``except BaseException`` cleanup point owns the
    # session-file unlink as well, so a fault injected after the socketpair
    # exists cannot leave the handshake file behind across the boundary.
    assert not session_file.exists(), (
        "session file at "
        f"{session_file} "
        "was not removed after the post-socketpair fault; the caller must "
        "unlink it in its except handler before re-raising."
    )
    # The caller's single ``except BaseException`` cleanup point must have
    # closed both socket ends before re-raising, so the test process's
    # open-fd count is unchanged.
    if fds_before >= 0:
        assert _count_open_fds() == fds_before
