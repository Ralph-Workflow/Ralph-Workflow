"""Lock liveness against REAL processes and a real git repository (Linux /proc)."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.git import _lock_recovery as lr

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = [
    pytest.mark.skipif(
        not sys.platform.startswith("linux") or not Path("/proc/self/fd").is_dir(),
        reason="exercises /proc process inspection",
    ),
    # Real processes and a real /proc scan: the 1s default per-test cap is for fakes.
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(30),
]

_HOLDER = textwrap.dedent(
    """
    import ctypes, os, sys, time
    lock, mode = sys.argv[1], sys.argv[2]
    if mode == "nodump":
        ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE=0
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    print("ready", flush=True)
    time.sleep(120)
    """
)


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(
        main,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "i",
    )
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", str(wt), "-b", "feat")
    return wt


def _private_lock(wt: Path) -> Path:
    gitdir = _git(wt, "rev-parse", "--absolute-git-dir").strip()
    return Path(gitdir) / "index.lock"


def _age(lock: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(lock, (then, then))


@pytest.fixture
def holder(tmp_path: Path) -> Iterator[object]:
    procs: list[subprocess.Popen[str]] = []

    def start(lock: Path, mode: str = "plain") -> subprocess.Popen[str]:
        script = tmp_path / "holder.py"
        script.write_text(_HOLDER)
        proc = subprocess.Popen(
            [sys.executable, "-I", str(script), str(lock), mode],
            stdout=subprocess.PIPE,
            text=True,
        )
        procs.append(proc)
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "ready"
        return proc

    yield start
    for proc in procs:
        proc.kill()
        proc.wait()


def test_orphaned_worktree_lock_is_proven_dead_and_removed(worktree: Path) -> None:
    lock = _private_lock(worktree)
    lock.write_text("orphan")
    _age(lock, 5000)
    err = Exception(f"fatal: Unable to create '{lock}': File exists.")
    assert lr._lock_has_no_live_holder(lock, lock.parent) == "dead"
    assert lr._recover_stale_git_lock("stage_all", err) is True
    assert not lock.exists()


def test_lock_held_open_by_a_live_process_is_never_removed(worktree: Path, holder) -> None:  # type: ignore[no-untyped-def]
    lock = _private_lock(worktree)
    holder(lock)
    _age(lock, 5000)
    err = Exception(f"fatal: Unable to create '{lock}': File exists.")
    assert lr._lock_has_no_live_holder(lock, lock.parent) == "live"
    assert lr._recover_stale_git_lock("stage_all", err) is False
    assert lock.exists()


def test_non_dumpable_live_holder_is_not_declared_dead(worktree: Path, holder) -> None:  # type: ignore[no-untyped-def]
    """A holder whose fd table we cannot read must not be ruled out by guesswork."""
    lock = _private_lock(worktree)
    proc = holder(lock, "nodump")
    assert not os.access(f"/proc/{proc.pid}/fd", os.R_OK)
    assert lr._lock_has_no_live_holder(lock, lock.parent) == "unknown"


def test_idle_read_only_git_helper_in_the_repo_does_not_pin_an_orphan(worktree: Path) -> None:
    """A persistent ``git cat-file --batch`` (GitPython, IDEs) never takes the lock."""
    lock = _private_lock(worktree)
    lock.write_text("orphan")
    _age(lock, 5000)
    helper = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=worktree,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    try:
        assert lr._lock_has_no_live_holder(lock, lock.parent) == "dead"
    finally:
        helper.kill()
        helper.wait()


def test_lock_replaced_during_the_probe_is_not_removed(worktree: Path, holder) -> None:  # type: ignore[no-untyped-def]
    lock = _private_lock(worktree)
    lock.write_text("orphan")
    _age(lock, 5000)
    err = Exception(f"fatal: Unable to create '{lock}': File exists.")

    def probe_then_swap(path: Path, _gitdir: Path) -> str:
        path.unlink()
        holder(path)
        _age(path, 5000)
        return "dead"

    assert lr._recover_stale_git_lock("stage_all", err, liveness_fn=probe_then_swap) is False  # type: ignore[arg-type]
    assert lock.exists()


def _orphan(lock: Path) -> None:
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("orphan")
    _age(lock, 5000)


@pytest.mark.parametrize(
    ("args", "lock_rel"),
    [
        (["update-ref", "refs/heads/feat", "HEAD"], "refs/heads/feat.lock"),
        (["reset", "-q", "--mixed", "HEAD"], "index.lock"),
        (["checkout", "-q", "-b", "newbranch"], "HEAD.lock"),
        (["config", "--local", "x.y", "z"], "config.lock"),
        (["merge", "--no-edit", "HEAD"], "index.lock"),
    ],
)
def test_run_git_clears_an_orphaned_lock_for_any_command(
    worktree: Path, args: list[str], lock_rel: str
) -> None:
    """No run_git caller has to know about locks: the one spawn point recovers."""
    from ralph.git.subprocess_runner import run_git

    gitdir = Path(_git(worktree, "rev-parse", "--absolute-git-dir").strip())
    common = Path(_git(worktree, "rev-parse", "--git-common-dir").strip())
    common = common if common.is_absolute() else (worktree / common).resolve()
    lock = (
        common if lock_rel.startswith("refs/") or lock_rel == "config.lock" else gitdir
    ) / lock_rel
    _orphan(lock)
    result = run_git(args, cwd=worktree, label="test")
    assert result.returncode == 0, result.stderr
    assert not lock.exists()


def test_run_git_does_not_steal_a_lock_a_live_process_holds(
    worktree: Path,
    holder,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    from ralph.git.subprocess_runner import run_git

    monkeypatch.setattr(lr, "_LIVE_HOLDER_PATIENCE_S", 1.0)
    monkeypatch.setattr(lr, "_LIVE_HOLDER_POLL_S", 0.2)
    lock = _private_lock(worktree)
    holder(lock)
    _age(lock, 5000)
    result = run_git(["reset", "-q", "--mixed", "HEAD"], cwd=worktree, label="test")
    assert result.returncode != 0
    assert lock.exists()


def _slow_ref_transaction_hook(repo_git_dir: Path) -> None:
    """A reference-transaction hook that holds the ref lock (fd closed) for a while."""
    hooks = repo_git_dir / "hooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "reference-transaction"
    hook.write_text('#!/bin/sh\n[ "$1" = prepared ] && sleep 8\nexit 0\n')
    hook.chmod(0o755)


@pytest.mark.parametrize("mode", ["renamed-binary", "relative-git-dir", "linked-worktree-cwd"])
def test_a_git_holding_a_ref_lock_without_an_open_fd_is_live(
    worktree: Path, tmp_path: Path, mode: str
) -> None:
    """Git holds ``refs/heads/x.lock`` with no fd open between prepare and commit."""
    import shutil

    common = Path(_git(worktree, "rev-parse", "--git-common-dir").strip())
    common = common if common.is_absolute() else (worktree / common).resolve()
    _slow_ref_transaction_hook(common)
    lock = common / "refs" / "heads" / "x.lock"
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    if mode == "renamed-binary":
        binary = tmp_path / "g"
        shutil.copy(shutil.which("git") or "git", binary)
        cmd, cwd = (
            [str(binary), "--git-dir", str(common), "update-ref", "refs/heads/x", "HEAD"],
            tmp_path,
        )
    elif mode == "relative-git-dir":
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        rel = os.path.relpath(common, elsewhere)
        cmd, cwd = ["git", "--git-dir", rel, "update-ref", "refs/heads/x", "HEAD"], elsewhere
    else:
        cmd, cwd = ["git", "update-ref", "refs/heads/x", "HEAD"], worktree
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while not lock.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert lock.exists(), "the hook window never opened"
        _age(lock, 60)
        assert lr._lock_has_no_live_holder(lock, common) != "dead"
    finally:
        proc.kill()
        proc.wait()
