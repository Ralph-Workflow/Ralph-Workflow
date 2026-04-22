"""Tests for process_phase_scope: verifies phase-labeled processes are reaped on scope exit."""

from __future__ import annotations

import contextlib
import sys
import time
from typing import TYPE_CHECKING

import psutil
import pytest

import ralph.process.manager as _mgr
from ralph.git.subprocess_runner import run_git
from ralph.process import process_phase_scope  # validates public package export
from ralph.process.manager import (
    ProcessManager,
    ProcessManagerPolicy,
    ProcessStatus,
    get_process_manager,
    reset_process_manager,
)

if TYPE_CHECKING:
    from pathlib import Path

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3, kill_followup_timeout_s=0.5, log_events=False
)

PYTHON = sys.executable


@pytest.fixture(autouse=True)
def _reset_pm():
    reset_process_manager()
    yield
    with contextlib.suppress(Exception):
        get_process_manager().shutdown_all(grace_period_s=0)
    reset_process_manager()


def _pid_gone(pid: int, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return True
        time.sleep(0.02)
    return not psutil.pid_exists(pid)


def test_phase_scope_kills_labeled_processes() -> None:
    """All processes labeled 'phase:review' are killed when the scope exits."""
    pm = ProcessManager(policy=_FAST_POLICY)
    handles = [
        pm.spawn(
            [PYTHON, "-c", "import time; time.sleep(30)"],
            label="phase:review",
        )
        for _ in range(3)
    ]
    pids = [h.record.pid for h in handles]

    original_singleton = _mgr._singleton
    _mgr._singleton = pm
    try:
        with process_phase_scope("review"):
            pass
    finally:
        _mgr._singleton = original_singleton

    for handle in handles:
        assert handle.record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED)

    for pid in pids:
        assert _pid_gone(pid), f"PID {pid} still alive after phase scope exit"


def test_phase_scope_does_not_kill_other_labels() -> None:
    """Processes with non-matching labels are not affected by the phase scope."""
    pm = ProcessManager(policy=_FAST_POLICY)
    target = pm.spawn(
        [PYTHON, "-c", "import time; time.sleep(30)"],
        label="phase:review",
    )
    bystander = pm.spawn(
        [PYTHON, "-c", "import time; time.sleep(30)"],
        label="other:process",
    )

    original_singleton = _mgr._singleton
    _mgr._singleton = pm
    try:
        with process_phase_scope("review"):
            pass
    finally:
        _mgr._singleton = original_singleton

    assert target.record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED)
    assert bystander.record.status == ProcessStatus.RUNNING

    bystander.terminate(grace_period_s=0)


def test_run_git_phase_parameter_constructs_phase_scoped_label(tmp_git_repo: Path) -> None:
    """run_git with phase= creates a 'phase:<phase>:git:<label>' record label."""
    pm = ProcessManager(policy=_FAST_POLICY)
    original_singleton = _mgr._singleton
    _mgr._singleton = pm
    try:
        run_git(
            ["rev-parse", "HEAD"],
            cwd=tmp_git_repo,
            label="rev-parse",
            phase="development",
        )
    finally:
        _mgr._singleton = original_singleton

    labels = [r.label for r in pm._records.values()]
    assert "phase:development:git:rev-parse" in labels, (
        f"Expected 'phase:development:git:rev-parse' in labels, got: {labels}"
    )


def test_run_git_without_phase_uses_plain_label(tmp_git_repo: Path) -> None:
    """run_git without phase= uses the label as-is."""
    pm = ProcessManager(policy=_FAST_POLICY)
    original_singleton = _mgr._singleton
    _mgr._singleton = pm
    try:
        run_git(
            ["rev-parse", "HEAD"],
            cwd=tmp_git_repo,
            label="git:rev-parse",
        )
    finally:
        _mgr._singleton = original_singleton

    labels = [r.label for r in pm._records.values()]
    assert "git:rev-parse" in labels, (
        f"Expected 'git:rev-parse' in labels, got: {labels}"
    )
