"""Tests for process_phase_scope: verifies phase-labeled processes are reaped on scope exit."""

from __future__ import annotations

import contextlib
import itertools
import sys
from typing import TYPE_CHECKING

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
from ralph.testing.fake_process import make_sync_process_factory

if TYPE_CHECKING:
    from pathlib import Path

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3, kill_followup_timeout_s=0.5, log_events=False
)


@pytest.fixture(autouse=True)
def _reset_pm():
    reset_process_manager()
    yield
    with contextlib.suppress(Exception):
        get_process_manager().shutdown_all(grace_period_s=0)
    reset_process_manager()


def test_phase_scope_kills_labeled_processes() -> None:
    """All processes labeled 'phase:review' are killed when the scope exits."""
    sync_factory = make_sync_process_factory(
        itertools.count(1), returncode=None
    )
    pm = ProcessManager(policy=_FAST_POLICY, sync_process_factory=sync_factory)
    handles = [
        pm.spawn(
            [sys.executable, "-c", "pass"],
            label="phase:review",
        )
        for _ in range(3)
    ]

    original_singleton = _mgr._singleton
    _mgr._singleton = pm
    try:
        with process_phase_scope("review"):
            pass
    finally:
        _mgr._singleton = original_singleton

    for handle in handles:
        assert handle.record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED)


def test_phase_scope_does_not_kill_other_labels() -> None:
    """Processes with non-matching labels are not affected by the phase scope."""
    sync_factory = make_sync_process_factory(
        itertools.count(1), returncode=None
    )
    pm = ProcessManager(policy=_FAST_POLICY, sync_process_factory=sync_factory)
    target = pm.spawn(
        [sys.executable, "-c", "pass"],
        label="phase:review",
    )
    bystander = pm.spawn(
        [sys.executable, "-c", "pass"],
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
    sync_factory = make_sync_process_factory(
        itertools.count(1), returncode=0
    )
    pm = ProcessManager(policy=_FAST_POLICY, sync_process_factory=sync_factory)
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
    sync_factory = make_sync_process_factory(
        itertools.count(1), returncode=0
    )
    pm = ProcessManager(policy=_FAST_POLICY, sync_process_factory=sync_factory)
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
