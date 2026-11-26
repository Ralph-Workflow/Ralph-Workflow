from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import pytest

from ralph.process.session_orphan_reaper import reap_session_orphans

if TYPE_CHECKING:
    from pathlib import Path

_OWNER = 4242
_SESSION = "development-aaaa1111"


def _proc(root: Path, pid: int, ppid: int, env: dict[str, str]) -> None:
    entry = root / str(pid)
    entry.mkdir()
    (entry / "environ").write_bytes(b"".join(f"{k}={v}".encode() + b"\0" for k, v in env.items()))
    (entry / "stat").write_text(f"{pid} (proc) S {ppid} 0 0 0 0\n", encoding="utf-8")


def _session_env(session: str = _SESSION, owner: int = _OWNER) -> dict[str, str]:
    return {"MCP_SESSION_ID": session, "RALPH_MCP_OWNER_PID": str(owner), "PATH": "/bin"}


def test_reaps_escaped_processes_of_the_ended_session_and_their_descendants(
    tmp_path: Path,
) -> None:
    _proc(tmp_path, 100, 1, _session_env())
    _proc(tmp_path, 101, 100, {"PATH": "/bin"})
    _proc(tmp_path, 200, 1, _session_env(session="development-bbbb2222"))
    _proc(tmp_path, 300, 1, _session_env(owner=9999))
    _proc(tmp_path, 400, 1, {"PATH": "/bin"})
    sent: list[tuple[int, int]] = []

    reaped = reap_session_orphans(
        _SESSION,
        owner_pid=_OWNER,
        proc_root=tmp_path,
        signal_fn=lambda pid, sig: sent.append((pid, sig)),
        alive_fn=lambda _pid: False,
        sleep=lambda _s: None,
    )

    assert reaped == 2
    assert sorted(pid for pid, sig in sent if sig == signal.SIGTERM) == [100, 101]
    assert not [pid for pid, sig in sent if sig == signal.SIGKILL]


def test_kills_survivors_after_the_grace_period(tmp_path: Path) -> None:
    _proc(tmp_path, 100, 1, _session_env())
    sent: list[tuple[int, int]] = []

    reap_session_orphans(
        _SESSION,
        owner_pid=_OWNER,
        proc_root=tmp_path,
        signal_fn=lambda pid, sig: sent.append((pid, sig)),
        alive_fn=lambda _pid: True,
        sleep=lambda _s: None,
        grace_seconds=0.05,
    )

    assert sent == [(100, signal.SIGTERM), (100, signal.SIGKILL)]


def test_never_signals_the_engine_itself(tmp_path: Path) -> None:
    _proc(tmp_path, os.getpid(), 1, _session_env())
    sent: list[tuple[int, int]] = []

    reaped = reap_session_orphans(
        _SESSION,
        owner_pid=_OWNER,
        proc_root=tmp_path,
        signal_fn=lambda pid, sig: sent.append((pid, sig)),
        alive_fn=lambda _pid: False,
        sleep=lambda _s: None,
    )

    assert reaped == 0
    assert sent == []


def test_an_empty_session_id_reaps_nothing(tmp_path: Path) -> None:
    _proc(tmp_path, 100, 1, _session_env(session=""))

    assert (
        reap_session_orphans(
            "",
            owner_pid=_OWNER,
            proc_root=tmp_path,
            signal_fn=lambda _pid, _sig: pytest.fail("must not signal"),
            alive_fn=lambda _pid: False,
            sleep=lambda _s: None,
        )
        == 0
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(20)
def test_a_real_setsid_detached_child_of_the_session_is_reaped() -> None:
    env = {**os.environ, **_session_env(owner=os.getpid())}
    launcher = subprocess.run(
        [
            sys.executable,
            "-c",
            "import subprocess,sys;"
            "p=subprocess.Popen(['sleep','300'],start_new_session=True,"
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
            "print(p.pid)",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    detached = int(launcher.stdout.strip())
    try:
        reaped = reap_session_orphans(_SESSION, owner_pid=os.getpid(), grace_seconds=3.0)

        assert reaped >= 1
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                os.kill(detached, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("the detached session child survived the reaper")
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(detached, signal.SIGKILL)


def test_reaps_session_processes_that_carry_no_owner_pid(tmp_path: Path) -> None:
    _proc(tmp_path, 100, 1, {"MCP_SESSION_ID": _SESSION, "PATH": "/bin"})
    _proc(tmp_path, 200, 1, {"MCP_SESSION_ID": _SESSION, "RALPH_MCP_OWNER_PID": "9999"})
    sent: list[tuple[int, int]] = []

    reaped = reap_session_orphans(
        _SESSION,
        owner_pid=_OWNER,
        proc_root=tmp_path,
        signal_fn=lambda pid, sig: sent.append((pid, sig)),
        alive_fn=lambda _pid: False,
        sleep=lambda _s: None,
    )

    assert reaped == 1
    assert sent == [(100, signal.SIGTERM)]
