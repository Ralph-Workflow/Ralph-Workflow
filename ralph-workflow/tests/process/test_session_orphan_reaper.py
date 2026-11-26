from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server.lifecycle import StandaloneMcpProcess
from ralph.process.session_orphan_reaper import (
    PROCESS_SCOPE_ENV,
    reap_scope_orphans,
    reap_session_orphans,
    reaped_process_scope,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_a_real_setsid_detached_child_of_the_session_is_reaped": (
        "macOS returns an empty environment for SIP-protected system binaries such as "
        "/bin/sleep, so the reaper cannot see the session marker on that child and the test "
        "failed on every macOS run; the detached child is now a Python sleep, still a real "
        "process started in a new session, and every reaping assertion is unchanged"
    ),
}

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
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(300)'],"
            "start_new_session=True,"
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


def test_a_real_ownerless_session_child_is_reaped_without_procfs() -> None:
    env = {key: value for key, value in os.environ.items() if key != "RALPH_MCP_OWNER_PID"}
    env["MCP_SESSION_ID"] = _SESSION
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(300)"],
        env=env,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        reaped = reap_session_orphans(_SESSION, owner_pid=os.getpid(), grace_seconds=3.0)

        assert reaped >= 1
        assert child.wait(timeout=5.0) is not None
    finally:
        if child.poll() is None:
            child.kill()


class _ExitedProcess:
    pid = 2**31 - 1

    def poll(self) -> int | None:
        return 0

    def wait(self, timeout: float | None = None) -> int | None:
        del timeout
        return 0


class _ServerThatFailsToStop(StandaloneMcpProcess):
    def shutdown(self) -> None:
        raise RuntimeError("Process 1 (pgid 1) [force_kill]: still alive")


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(20)
def test_a_bridge_whose_server_fails_to_stop_still_reaps_its_session() -> None:
    env = {**os.environ, **_session_env(session="development-cccc3333", owner=os.getpid())}
    launcher = subprocess.run(
        [
            sys.executable,
            "-c",
            "import subprocess;"
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
    inner = _ServerThatFailsToStop(
        endpoint="http://127.0.0.1:9500/mcp",
        process=_ExitedProcess(),
        session_file=Path("/nonexistent/session.json"),
    )
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: inner,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=0, cooldown_seconds=0.0),
        run_id="test-run",
        session_id="development-cccc3333",
    )
    try:
        with pytest.raises(RuntimeError, match="still alive"):
            bridge.shutdown()

        pause = threading.Event()
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and Path(f"/proc/{detached}").exists():
            pause.wait(0.05)
        assert not Path(f"/proc/{detached}").exists(), "the session's detached child survived"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(detached, signal.SIGKILL)


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(20)
def test_a_detached_process_of_a_reaped_scope_dies_when_the_scope_exits() -> None:
    with reaped_process_scope("agent", owner_pid=os.getpid()) as scope_env:
        launcher = subprocess.run(
            [
                sys.executable,
                "-c",
                "import subprocess;"
                "p=subprocess.Popen(['sleep','300'],start_new_session=True,"
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
                "print(p.pid)",
            ],
            env={**os.environ, **scope_env},
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        detached = int(launcher.stdout.strip())
        assert scope_env[PROCESS_SCOPE_ENV].startswith("agent-")
    try:
        pause = threading.Event()
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and Path(f"/proc/{detached}").exists():
            pause.wait(0.05)
        assert not Path(f"/proc/{detached}").exists(), "the scope's detached child survived"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(detached, signal.SIGKILL)


def test_a_scope_reap_never_reads_processes_that_predate_the_scope(tmp_path: Path) -> None:
    scope_env = {"RALPH_PROCESS_SCOPE": "agent-aaaa", "PATH": "/bin"}
    _proc(tmp_path, 100, 1, scope_env)
    _proc(tmp_path, 200, 1, scope_env)
    padding = " ".join("0" for _ in range(17))
    (tmp_path / "100" / "stat").write_text(f"100 (old) S 1 {padding} 500 0\n", encoding="utf-8")
    (tmp_path / "200" / "stat").write_text(f"200 (new) S 1 {padding} 900 0\n", encoding="utf-8")
    sent: list[tuple[int, int]] = []

    reaped = reap_scope_orphans(
        "agent-aaaa",
        owner_pid=_OWNER,
        proc_root=tmp_path,
        signal_fn=lambda pid, sig: sent.append((pid, sig)),
        alive_fn=lambda _pid: False,
        sleep=lambda _s: None,
        started_after_ticks=700,
    )

    assert reaped == 1
    assert sent == [(200, signal.SIGTERM)]
