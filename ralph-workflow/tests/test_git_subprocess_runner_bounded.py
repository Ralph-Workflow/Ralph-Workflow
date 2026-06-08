"""run_git must fail closed: a bounded default timeout and non-interactive git.

Git invoked outside ralph/mcp/ (operations, rebase, vendor-drift checks) was
unbounded and could block forever on a credential/network prompt — a hang
vector behind the 5-hour runaway. run_git now substitutes a default timeout when
a caller gives none, and always runs git in batch mode (GIT_TERMINAL_PROMPT=0)
so a prompt fails fast instead of hanging.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git import subprocess_runner
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.timeout_defaults import GIT_SUBPROCESS_TIMEOUT_SECONDS

if TYPE_CHECKING:
    import pytest

    from ralph.process.manager import SpawnOptions


class _FakeProc:
    def __init__(self) -> None:
        self.stdout: object = None
        self.stderr: object = None
        self.returncode: int | None = 0
        self._proc: object = object()
        self.recorded_timeout: float | None = -1.0

    def communicate_and_cleanup(
        self, timeout: float | None = None, cleanup_grace_period_s: float = 0.0
    ) -> tuple[bytes, bytes]:
        del cleanup_grace_period_s
        self.recorded_timeout = timeout
        return (b"", b"")

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0

    def terminate(self, grace_period_s: float = 0.0) -> None:
        del grace_period_s


class _FakeManager:
    def __init__(self) -> None:
        self.spawn_options: SpawnOptions | None = None
        self.proc = _FakeProc()

    def spawn(self, cmd: object, options: SpawnOptions) -> _FakeProc:
        del cmd
        self.spawn_options = options
        return self.proc


def _install_fake_manager(monkeypatch: pytest.MonkeyPatch) -> _FakeManager:
    manager = _FakeManager()
    monkeypatch.setattr(subprocess_runner, "get_process_manager", lambda: manager)
    return manager


def test_run_git_default_timeout_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _install_fake_manager(monkeypatch)
    run_git(["status"], cwd=None, label="test")
    assert manager.proc.recorded_timeout == GIT_SUBPROCESS_TIMEOUT_SECONDS


def test_run_git_sets_git_terminal_prompt_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _install_fake_manager(monkeypatch)
    run_git(["status"], cwd=None, label="test")
    options = manager.spawn_options
    assert options is not None
    assert options.env is not None
    assert options.env["GIT_TERMINAL_PROMPT"] == "0"


def test_run_git_explicit_timeout_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _install_fake_manager(monkeypatch)
    run_git(["status"], cwd=None, label="test", options=GitRunOptions(timeout=7.5))
    assert manager.proc.recorded_timeout == 7.5
