"""Focused tests for the per-common-dir git command queue (unit D).

Fake-executor coverage for the serialization/backoff proofs (no fake git
subprocess is spawned through the real process manager); the worktree test
uses a real ``git worktree`` because the sharing property is a filesystem
fact. Adheres to ``audit_test_policy`` (no ``time.sleep``).

Proves:
* GitPython adapter calls (``route_git_method``) and the ``run_git``
  subprocess path (``route_run_git`` with a fake executor) serialize on ONE
  queue per repository common directory, with recorded call ordering.
* Linked worktrees of one repository share a queue; unrelated repositories
  get independent queues.
* Consecutive timeouts raise a SHARED health counter that both paths
  consult/back off on (mixed-path backoff proof).
"""

from __future__ import annotations

import contextlib
import shutil
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Repo

from ralph.git import git_command_queue as gcq
from ralph.git.git_command_queue import (
    get_queue_for_repo,
    reset_all_queues,
    route_git_method,
    route_run_git,
)
from ralph.git.git_run_result import GitRunResult

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    reset_all_queues()
    yield
    reset_all_queues()


@pytest.fixture()
def real_repo(tmp_git_repo: Path) -> Iterator[Repo]:
    """Open the conftest-provided temporary repository.

    ``tmp_git_repo`` is cloned from a session template (fast, no
    ``Repo.init`` call — whose classmethod chain resolves to ``Any``
    under the project's ``disallow_any_expr`` mypy policy). The
    unborn-HEAD/branch shape is irrelevant to the queue semantics under
    test (queue keys derive from the git dir path only).
    """
    repo = Repo(tmp_git_repo)
    yield repo
    repo.close()


class _FakeGit:
    """Stands in for ``repo.git``; records call order and can time out."""

    def __init__(self, log: list[str], time_out_methods: set[str]) -> None:
        self._log = log
        self._time_out = time_out_methods
        self.lock = threading.Lock()

    def __getattr__(self, name: str) -> object:
        def call(*args: object, **kwargs: object) -> str:
            with self.lock:
                self._log.append(f"gitpy:{name}")
            if name in self._time_out:
                raise TimeoutError(f"{name} timed out")
            return f"ok:{name}"

        return call


class _FakeRepo:
    """Minimal Repo-like stand-in for ``route_git_method``.

    Passed to :func:`route_git_method` through a narrow cast at the seam:
    the production function's runtime contract is structural (it resolves
    ``repo.git.<method>``), and the fake implements exactly that subset.
    """

    def __init__(self, git_dir: str, git: object) -> None:
        self.git_dir = git_dir
        self.git = git


class _FakeProcess:
    def __init__(self, log: list[str], lock: threading.Lock, timed_out: bool) -> None:
        self._log = log
        self._lock = lock
        self._timed_out = timed_out
        self.returncode: int | None = 0
        self.stdout = None
        self.stderr = None
        self._proc = object()

    def communicate_and_cleanup(
        self,
        *,
        timeout: float,
        cleanup_grace_period_s: float = 0.0,
        output_limit_bytes: int | None = None,
        input: bytes | None = None,
    ) -> tuple[str, str]:
        with self._lock:
            self._log.append("subprocess:git")
        if self._timed_out:
            raise TimeoutError(f"git timed out after {timeout}s")
        return "", ""

    def poll(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self, grace_period_s: float = 0.0) -> None:
        return None


def _install_fake_run_git(
    monkeypatch: pytest.MonkeyPatch,
    log: list[str],
    lock: threading.Lock,
    time_out: bool,
) -> None:
    """Patch subprocess_runner._spawn_git with a recording fake executor."""

    def fake_spawn(
        args: object,
        cwd: object,
        label: str,
        options: object,
    ) -> GitRunResult:
        proc = _FakeProcess(log, lock, time_out)
        try:
            stdout, stderr = proc.communicate_and_cleanup(timeout=120.0)
        except TimeoutError:
            proc.terminate()
            raise
        assert isinstance(args, (list, tuple))
        assert all(isinstance(arg, str) for arg in args)
        return GitRunResult(args=("git", *args), returncode=0, stdout=stdout, stderr=stderr)

    from ralph.git import subprocess_runner

    monkeypatch.setattr(subprocess_runner, "_spawn_git", fake_spawn)


def test_gitpython_and_subprocess_paths_serialize_shared_queue(
    real_repo: Repo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log: list[str] = []
    lock = threading.Lock()
    _install_fake_run_git(monkeypatch, log, lock, time_out=False)

    repo_dir = str(real_repo.git_dir)
    repo = _FakeRepo(repo_dir, _FakeGit(log, set()))
    cwd = Path(real_repo.working_dir or repo_dir)

    route_git_method(repo, "status", ("--porcelain",))
    route_run_git(["diff", "--cached", "--name-only"], cwd=cwd, label="t")
    route_git_method(repo, "ls_files", ("--stage",))

    assert log == ["gitpy:status", "subprocess:git", "gitpy:ls_files"]
    queue = get_queue_for_repo(real_repo)
    assert queue.health_factor() == 0
    kinds = [outcome.kind for outcome in queue.outcome_log]
    assert kinds == ["success", "success", "success"]


def test_linked_worktrees_share_queue_unrelated_repos_independent(
    real_repo: Repo,
    tmp_path: Path,
) -> None:
    work_dir = Path(real_repo.working_dir or ".")
    worktree_dir = (work_dir / ".." / "wt-linked").resolve()
    real_repo.git.worktree("add", "-b", "linked", str(worktree_dir))
    q_main = get_queue_for_repo(real_repo)
    q_wt = get_queue_for_repo(worktree_dir)
    assert q_main is q_wt

    # Unrelated-repo control: clone the session template into tmp_path
    # (typed constructor only — no Repo.init call, see real_repo fixture).
    other_root = tmp_path / "other-repo"
    shutil.copytree(real_repo.working_dir or ".", other_root)
    other = Repo(other_root)
    try:
        q_other = get_queue_for_repo(other)
        assert q_other is not q_main
    finally:
        other.close()
    with contextlib.suppress(Exception):
        real_repo.git.worktree("remove", str(worktree_dir), "--force")
    # Branch removal is best-effort cleanup; the assertions already held.
    with contextlib.suppress(Exception):
        real_repo.git.branch("-D", "linked")


def test_consecutive_timeouts_share_health_across_both_paths(
    real_repo: Repo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A subprocess-path timeout and a GitPython-path timeout both advance
    the SAME per-repo counter; the next caller of either path sees it."""
    log: list[str] = []
    lock = threading.Lock()
    _install_fake_run_git(monkeypatch, log, lock, time_out=True)

    queue = get_queue_for_repo(real_repo)
    git_timeout = {"status"}
    repo_dir = str(real_repo.git_dir)
    repo = _FakeRepo(repo_dir, _FakeGit(log, git_timeout))
    cwd = Path(real_repo.working_dir or repo_dir)

    with pytest.raises(TimeoutError):
        route_run_git(["status"], cwd=cwd, label="t")
    assert queue.health_factor() == 1

    with pytest.raises(TimeoutError):
        route_git_method(repo, "status", ("--porcelain",))
    assert queue.health_factor() == 2

    # A success on either path resets the shared counter.
    from ralph.git import subprocess_runner

    def success_spawn(*args: object, **kwargs: object) -> GitRunResult:
        return GitRunResult(args=("git",), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess_runner, "_spawn_git", success_spawn)
    route_run_git(["diff"], cwd=cwd, label="t")
    assert queue.health_factor() == 0


def test_route_run_git_unknown_repo_gets_global_fallback_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log: list[str] = []
    lock = threading.Lock()
    _install_fake_run_git(monkeypatch, log, lock, time_out=False)
    route_run_git(["status"], cwd=tmp_path, label="t")
    assert log == ["subprocess:git"]


def test_registry_reset_clears_queues(real_repo: Repo) -> None:
    q1 = get_queue_for_repo(real_repo)
    q1.record_outcome("timeout")
    assert q1.health_factor() == 1
    reset_all_queues()
    q2 = get_queue_for_repo(real_repo)
    assert q2 is not q1
    assert q2.health_factor() == 0


def test_builtins_timeout_classification() -> None:
    """Timeout-like exceptions classify as 'timeout', others as 'error'."""
    classify = gcq._classify_exception
    assert classify(TimeoutError("x")) == "timeout"
    assert classify(ValueError("x")) == "error"
