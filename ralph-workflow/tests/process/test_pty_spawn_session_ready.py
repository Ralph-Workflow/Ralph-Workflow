from __future__ import annotations

import os
import sys
import time

import psutil
import pytest

from ralph.process.manager import ProcessManager, ProcessManagerPolicy, PtySpawnOptions
from ralph.process.pty import spawn_pty_process
from ralph.process.teardown import teardown_subtree

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

_SPAWNS = 12
_QUIET_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.1,
    kill_followup_timeout_s=0.1,
    log_events=False,
    enable_zombie_reaper=False,
)


def test_spawn_pty_process_returns_a_child_that_already_leads_its_session() -> None:
    own_pgid = os.getpgid(0)
    for _ in range(_SPAWNS):
        proc = spawn_pty_process(["/bin/sleep", "5"], cwd=None, env=None)
        try:
            child_pgid = os.getpgid(proc.pid)
            child_sid = os.getsid(proc.pid)
        finally:
            proc.kill()
            proc.wait(timeout=2.0)
            proc.close()
        assert child_pgid == proc.pid, (
            f"spawn returned before the child called setsid(): pgid={child_pgid} "
            f"pid={proc.pid} own_pgid={own_pgid}"
        )
        assert child_sid == proc.pid
        assert child_pgid != own_pgid


def test_spawn_pty_records_the_childs_own_group_not_ralphs() -> None:
    pm = ProcessManager(policy=_QUIET_POLICY)
    own_pgid = os.getpgid(0)
    for _ in range(_SPAWNS):
        handle = pm.spawn_pty(["/bin/sleep", "5"], PtySpawnOptions(label="invoke:pty-race"))
        try:
            recorded = handle.record.pgid
        finally:
            handle.terminate(grace_period_s=0.1)
        assert recorded == handle.pid != own_pgid


def _sleepers(tag: str) -> list[psutil.Process]:
    found: list[psutil.Process] = []
    for proc in psutil.process_iter(["cmdline"]):
        cmdline = proc.info.get("cmdline")
        if cmdline == ["/bin/sleep", tag]:
            found.append(proc)
    return found


def test_teardown_reaps_a_session_member_that_outlived_the_pty_leader() -> None:
    pm = ProcessManager(policy=_QUIET_POLICY)
    tag = f"77.{os.getpid() % 1000:03d}"
    handle = pm.spawn_pty(
        ["/bin/sh", "-c", f"trap '' HUP; /bin/sleep {tag} & sleep 0.2"],
        PtySpawnOptions(label="invoke:pty-leak"),
    )
    try:
        handle.wait(timeout=5.0)
        teardown_subtree(handle.pid, issuer="invoke:pty", kill_escalation_ms=500)
        deadline = time.monotonic() + 2.0
        survivors = _sleepers(tag)
        while survivors and time.monotonic() < deadline:
            time.sleep(0.02)
            survivors = _sleepers(tag)
        assert survivors == [], f"session member survived teardown: {survivors}"
    finally:
        for proc in _sleepers(tag):
            proc.kill()
        handle.close()


def test_exec_failure_is_reported_to_the_parent_as_oserror() -> None:
    missing = "/nonexistent/ralph-pty-spawn-target"
    with pytest.raises(FileNotFoundError) as excinfo:
        spawn_pty_process([missing], cwd=None, env=None)
    assert excinfo.value.filename == missing


def test_chdir_failure_is_reported_to_the_parent_as_oserror() -> None:
    missing_dir = "/nonexistent/ralph-pty-spawn-cwd"
    with pytest.raises(FileNotFoundError) as excinfo:
        spawn_pty_process([sys.executable, "-c", "pass"], cwd=missing_dir, env=None)
    assert excinfo.value.filename == missing_dir
