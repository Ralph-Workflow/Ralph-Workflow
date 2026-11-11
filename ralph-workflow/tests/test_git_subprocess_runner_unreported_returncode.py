from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from ralph.git import subprocess_runner
from ralph.git.subprocess_runner import GitRunOptions, run_git
from tests.test_git_subprocess_runner_bounded import _FakeManager, _FakeProc

if TYPE_CHECKING:
    from ralph.git.git_run_result import GitRunResult


def _install_unreporting_manager(monkeypatch: pytest.MonkeyPatch) -> _FakeManager:
    manager = _FakeManager(_FakeProc(returncode=None))
    monkeypatch.setattr(subprocess_runner, "get_process_manager", lambda: manager)
    return manager


def test_run_git_reports_nonzero_when_child_never_reported_a_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_unreporting_manager(monkeypatch)
    result: GitRunResult = run_git(["status", "--porcelain"], cwd=None, label="enumerate")
    assert result.returncode != 0
    assert result.returncode == subprocess_runner._UNREPORTED_RETURNCODE


def test_run_git_check_true_raises_for_unreported_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_unreporting_manager(monkeypatch)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        run_git(
            ["status", "--porcelain"],
            cwd=None,
            label="enumerate",
            options=GitRunOptions(check=True),
        )
    assert caught.value.returncode != 0
    assert caught.value.returncode == subprocess_runner._UNREPORTED_RETURNCODE


def test_unreported_returncode_constant_is_nonzero() -> None:
    assert subprocess_runner._UNREPORTED_RETURNCODE != 0
