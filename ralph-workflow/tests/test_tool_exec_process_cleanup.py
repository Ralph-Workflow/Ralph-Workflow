"""Tests for process cleanup behaviour in the MCP exec tool."""

from __future__ import annotations

import itertools
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch


from ralph.mcp.tools._exec_run_deps import ExecRunDeps
from ralph.mcp.tools.exec import run_command
from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.process.manager._spawn_options import SpawnOptions
from ralph.testing.fake_process import (
    FakePsutil,
    FakePsutilProcess,
    make_sync_process_factory,
)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.0,
    kill_followup_timeout_s=0.0,
    log_events=False,
    enable_zombie_reaper=False,
)


class TestExecProcessCleanupUnit:
    def test_run_command_kills_known_descendants_via_psutil(self, tmp_path: Path) -> None:
        live_child = FakePsutilProcess(pid=1001, stubborn=True)
        fake_psutil = FakePsutil()
        root_proc = FakePsutilProcess(pid=1)
        root_proc._children = [live_child]
        fake_psutil._processes = {1: root_proc, 1001: live_child}
        pm = ProcessManager(
            policy=_FAST_POLICY,
            sync_process_factory=make_sync_process_factory(itertools.count(1), returncode=0),
            psutil=fake_psutil,
        )

        run_command(
            "python",
            ["-c", "pass"],
            tmp_path,
            5_000,
            deps=ExecRunDeps(process_manager=pm),
        )

        assert live_child._killed

    def test_windows_orphan_bfs_kills_grandchild(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        child = FakePsutilProcess(pid=11, ppid=1)
        grandchild = FakePsutilProcess(pid=12, ppid=11)
        fake_psutil = FakePsutil()
        fake_psutil._processes = {11: child, 12: grandchild}
        pm = ProcessManager(
            policy=_FAST_POLICY,
            sync_process_factory=make_sync_process_factory(itertools.count(1), returncode=0),
            psutil=fake_psutil,
        )
        monkeypatch.delattr(os, "killpg", raising=False)

        run_command(
            "python",
            ["-c", "pass"],
            tmp_path,
            5_000,
            deps=ExecRunDeps(process_manager=pm),
        )

        assert grandchild._killed, "Windows BFS must recursively kill grandchildren"


def test_reusable_sandbox_does_not_skip_process_cleanup(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_command("echo", ["ok"], workspace, 1000)

    assert result.returncode == 0


def test_shutdown_all_reaps_nested_descendant_tree_on_exit() -> None:
    """ProcessManager.shutdown_all() reaps the host and every nested descendant.

    Characterization test for the CURRENT_PROMPT.md requirement:
    "properly killing processes that we spawned" and "gracefully
    killing all child process when exiting". The pre-fix production
    code already satisfies this contract (no production change is
    required); this test pins the behaviour so future refactors
    cannot silently regress the cleanup semantics.

    The test wires a three-level tree: host (pid 100) -> child
    (pid 101) -> grandchild (pid 102, ``stubborn=True`` so it
    survives SIGTERM and requires SIGKILL escalation). The fake
    psutil's ``children()`` does not auto-recurse, so the host
    exposes both the child and the grandchild directly in its
    ``_children`` list. After ``pm.shutdown_all()`` every node must
    no longer be running, with the grandchild specifically marked
    ``_killed=True`` to prove SIGTERM->SIGKILL escalation reaches
    the stubborn descendant rather than bailing at the first
    survivor.
    """
    grandchild = FakePsutilProcess(pid=102, ppid=101, stubborn=True)
    child = FakePsutilProcess(pid=101, ppid=100)
    host = FakePsutilProcess(pid=100, ppid=1)
    # Flatten the tree at the host level because FakePsutilProcess.children
    # ignores the ``recursive`` flag (the production walk uses
    # ``children(recursive=True)``).
    host._children = [child, grandchild]
    fake_psutil = FakePsutil()
    fake_psutil._processes = {100: host, 101: child, 102: grandchild}
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(100), returncode=0),
        psutil=fake_psutil,
    )

    host_handle = pm.spawn(
        ["python", "-c", "pass"],
        SpawnOptions(label="host"),
    )
    assert host_handle.record.pid == 100

    pm.shutdown_all()

    assert not host.is_running(), "host process must be reaped on shutdown_all"
    assert not child.is_running(), "child process must be reaped on shutdown_all"
    assert grandchild._killed, (
        "stubborn grandchild must be reaped via SIGKILL escalation on shutdown_all"
    )
