"""run_git must fail closed: a bounded default timeout and non-interactive git.

Git invoked outside ralph/mcp/ (operations, rebase, vendor-drift checks) was
unbounded and could block forever on a credential/network prompt — a hang
vector behind the 5-hour runaway. run_git now substitutes a default timeout when
a caller gives none, and always runs git in batch mode (GIT_TERMINAL_PROMPT=0)
so a prompt fails fast instead of hanging.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.git import subprocess_runner
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.timeout_defaults import GIT_SUBPROCESS_TIMEOUT_SECONDS, GIT_TERMINATE_GRACE_SECONDS

if TYPE_CHECKING:
    from ralph.process.manager import SpawnOptions


class _SyntheticSignalFault(BaseException):
    """A non-timeout, non-output-cap fault that escapes ``communicate``."""


class _FakeProc:
    # OpenCode's prompt is delivered on stdin, so a process fake must
    # satisfy the ``_SyncProcessLike`` protocol's ``stdin`` member.
    stdin = None

    def __init__(
        self,
        *,
        raise_from_communicate: BaseException | None = None,
        returncode: int | None = 0,
    ) -> None:
        self.stdout: object = None
        self.stderr: object = None
        self.returncode: int | None = returncode
        self._proc: object = object()
        self.recorded_timeout: float | None = -1.0
        self.recorded_kwargs: dict[str, object] = {}
        self.terminate_calls: list[float] = []
        self._raise_from_communicate = raise_from_communicate

    def communicate_and_cleanup(
        self,
        timeout: float | None = None,
        cleanup_grace_period_s: float = 0.0,
        output_limit_bytes: int | None = None,
        terminate_grace_period_s: float | None = None,
    ) -> tuple[bytes, bytes]:
        self.recorded_timeout = timeout
        self.recorded_kwargs["output_limit_bytes"] = output_limit_bytes
        self.recorded_kwargs["terminate_grace_period_s"] = terminate_grace_period_s
        del cleanup_grace_period_s
        if self._raise_from_communicate is not None:
            raise self._raise_from_communicate
        return (b"", b"")

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0

    def terminate(self, grace_period_s: float = 0.0) -> None:
        self.terminate_calls.append(grace_period_s)


class _FakeManager:
    def __init__(self, proc: _FakeProc | None = None) -> None:
        self.spawn_options: SpawnOptions | None = None
        self.proc = _FakeProc() if proc is None else proc

    def spawn(self, cmd: object, options: SpawnOptions) -> _FakeProc:
        del cmd
        self.spawn_options = options
        return self.proc


def _install_fake_manager(
    monkeypatch: pytest.MonkeyPatch,
    *,
    proc: _FakeProc | None = None,
) -> _FakeManager:
    manager = _FakeManager(proc)
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


def test_run_git_terminates_child_when_communicate_raises_arbitrary_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fault that is neither TimeoutExpired nor the output cap still kills git.

    ``communicate_and_cleanup`` can be unwound by a caller-turned signal — the
    SIGALRM per-test cap, SIGINT, CancelledError — or by an I/O fault on the
    pipes. Before the BaseException branch existed, the trailing ``finally``
    closed the pipes and ``__exit__``ed the Popen WITHOUT killing the child, so
    the git process survived ``run_git`` and kept writing into a repository
    nobody was reaping. An interrupted index write is the observed damage: the
    NEXT enumeration then exits 128 with "index file smaller than expected".
    """
    fault = _SyntheticSignalFault("simulated interrupt inside communicate")
    proc = _FakeProc(raise_from_communicate=fault)
    _install_fake_manager(monkeypatch, proc=proc)

    with pytest.raises(_SyntheticSignalFault) as caught:
        run_git(["status"], cwd=None, label="test")

    assert caught.value is fault
    # Git removes its *.lock files from a SIGTERM handler; a zero grace window
    # SIGKILLs it first and orphans index.lock for every later git call.
    assert proc.terminate_calls == [GIT_TERMINATE_GRACE_SECONDS]
    assert GIT_TERMINATE_GRACE_SECONDS > 0.0


def test_run_git_propagates_original_fault_even_when_terminate_itself_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing teardown must never mask the exception the caller must see."""

    class _UnkillableProc(_FakeProc):
        def terminate(self, grace_period_s: float = 0.0) -> None:
            del grace_period_s
            raise OSError("process group already reaped")

    fault = _SyntheticSignalFault("original caller fault")
    proc = _UnkillableProc(raise_from_communicate=fault)
    _install_fake_manager(monkeypatch, proc=proc)

    with pytest.raises(_SyntheticSignalFault) as caught:
        run_git(["status"], cwd=None, label="test")

    assert caught.value is fault
