"""An unreported git exit status must read as failure, never as success.

``run_git`` reports ``_UNREPORTED_RETURNCODE`` (SIGKILL's conventional ``-9``)
when the child never answered. The previous behaviour reported ``0``, which let
every fail-closed consumer of a ``GitRunResult`` — unswept-work capture,
staged-patch proofs, protected-path prompts — read "I could not ask git" as
"git says there is nothing here". Every ``if result.returncode:`` gate then
opened on an unproven answer, and ``check=True`` reported success.
"""

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
    """Install a fake manager whose git child exits without ever reporting."""
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
    """The sentinel itself must never be a success status."""
    assert subprocess_runner._UNREPORTED_RETURNCODE != 0
