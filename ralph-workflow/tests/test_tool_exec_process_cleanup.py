"""Tests for process cleanup behaviour in the MCP exec tool."""

from __future__ import annotations

import itertools
import os
from contextlib import nullcontext
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch


from ralph.mcp.tools._exec_run_deps import ExecRunDeps
from ralph.mcp.tools.exec import run_command
from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.testing.fake_process import (
    FakePsutil,
    FakePsutilProcess,
    make_sync_process_factory,
)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.0,
    kill_followup_timeout_s=0.0,
    log_events=False,
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
            sync_process_factory=make_sync_process_factory(
                itertools.count(1), returncode=0
            ),
            psutil=fake_psutil,
        )

        run_command(
            "python",
            ["-c", "pass"],
            tmp_path,
            5_000,
            deps=ExecRunDeps(
                process_manager=pm,
                overlay_factory=lambda _root: nullcontext(tmp_path),
            ),
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
            sync_process_factory=make_sync_process_factory(
                itertools.count(1), returncode=0
            ),
            psutil=fake_psutil,
        )
        monkeypatch.delattr(os, "killpg", raising=False)

        run_command(
            "python",
            ["-c", "pass"],
            tmp_path,
            5_000,
            deps=ExecRunDeps(
                process_manager=pm,
                overlay_factory=lambda _root: nullcontext(tmp_path),
            ),
        )

        assert grandchild._killed, "Windows BFS must recursively kill grandchildren"


def test_reusable_sandbox_does_not_skip_process_cleanup(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_command("echo", ["ok"], workspace, 1000)

    assert result.returncode == 0
