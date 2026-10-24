"""Tests for the /proc liveness probe that gates git stale-lock recovery.

Split out of ``tests/test_git_lock_recovery.py`` to keep both modules under
the 1000-line repo-structure limit. This module covers the PROBE: the
per-process ``/proc`` inspection, the bounded worker pool and the
platform-specific verdicts ("live" / "dead" / "unknown").
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest

from ralph.git._lock_recovery import (
    _PROBE_MAX_WORKERS,
    _get_probe_executor,
    _pid_has_gitdir_environ,
    _pid_in_repo_cwd,
    _reset_probe_executor,
)
from ralph.git.operations import (
    _lock_has_no_live_holder,
)


def test_liveness_probe_whole_timeout_0_5s(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whole-probe ThreadPoolExecutor timeout (0.5s) returns 'unknown'."""
    lock_path = tmp_path / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")
    gitdir = tmp_path / ".git"
    gitdir.mkdir()

    from concurrent.futures import ThreadPoolExecutor

    def fake_result(
        *args: object, timeout: float | None = None, **kwargs: object
    ) -> Literal["live", "dead", "unknown"]:
        assert timeout == 0.5
        raise TimeoutError("probe timed out")

    def fake_submit(self: object, fn: object, *args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(result=fake_result)

    monkeypatch.setattr(ThreadPoolExecutor, "submit", fake_submit)

    result = _lock_has_no_live_holder(lock_path, gitdir, _proc_root=tmp_path / "proc")
    assert result == "unknown", "Probe timeout must return 'unknown', not 'live' or 'dead'"


def test_liveness_probe_or_predicate_cwd_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Process with cwd in gitdir (no GIT_DIR, no fd link) returns 'live'."""
    lock_path = tmp_path / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    gitdir_common = tmp_path / ".git"

    fake_proc = tmp_path / "proc"
    fake_proc.mkdir()
    fake_pid_dir = fake_proc / str(os.getpid())
    fake_pid_dir.mkdir()
    (fake_pid_dir / "cwd").symlink_to(str(gitdir.resolve()))  # cwd -> gitdir (live signal)
    (fake_pid_dir / "fd").mkdir()  # empty fd dir (no lock open)
    (fake_pid_dir / "comm").write_text("git\n", encoding="utf-8")  # git process
    (fake_pid_dir / "environ").write_bytes(b"")  # no GIT_DIR

    def fake_run(
        cmd: object,
        *,
        timeout: float | None = None,
        capture_output: bool = False,
        check: bool = False,
    ) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout=b"")

    monkeypatch.setattr("ralph.git._lock_recovery.subprocess.run", fake_run)

    result = _lock_has_no_live_holder(lock_path, gitdir, gitdir_common, _proc_root=fake_proc)
    assert result == "live", "Process with cwd=gitdir must return 'live' (OR predicate)"


def test_liveness_probe_common_gitdir_holder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Process with cwd in common gitdir also returns 'live'."""
    lock_path = tmp_path / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")
    gitdir = tmp_path / "worktree" / ".git"
    gitdir.mkdir(parents=True)
    gitdir_common = tmp_path / "main" / ".git"
    gitdir_common.mkdir(parents=True)

    fake_proc = tmp_path / "proc"
    fake_proc.mkdir()
    fake_pid_dir = fake_proc / str(os.getpid())
    fake_pid_dir.mkdir()
    (fake_pid_dir / "cwd").symlink_to(str(gitdir_common.resolve()))  # cwd -> common gitdir
    (fake_pid_dir / "fd").mkdir()
    (fake_pid_dir / "comm").write_text("git\n", encoding="utf-8")  # git process
    (fake_pid_dir / "environ").write_bytes(b"")

    def fake_run(
        cmd: object,
        *,
        timeout: float | None = None,
        capture_output: bool = False,
        check: bool = False,
    ) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout=b"")

    monkeypatch.setattr("ralph.git._lock_recovery.subprocess.run", fake_run)

    result = _lock_has_no_live_holder(lock_path, gitdir, gitdir_common, _proc_root=fake_proc)
    assert result == "live", "Process with cwd=common-gitdir must return 'live'"


def test_liveness_probe_real_blocked_worker_elapsed_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung worker thread (e.g. 2s sleep) does not delay probe return beyond timeout."""
    lock_path = tmp_path / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")
    gitdir = tmp_path / ".git"
    gitdir.mkdir()

    gate = threading.Event()

    def hung_probe(*args: object, **kwargs: object) -> bool:
        gate.wait(timeout=2.0)
        return False

    monkeypatch.setattr("ralph.git.operations._probe_lsof_fuser", hung_probe)

    try:
        result = _lock_has_no_live_holder(
            lock_path, gitdir, probe_timeout_s=0.05, _proc_root=tmp_path / "proc"
        )
        assert result == "unknown", "Hung probe must fail-closed to 'unknown'"
    finally:
        gate.set()


def test_liveness_probe_holder_beyond_120_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe scans all PIDs without 120 cap and detects holder at PID > 120."""
    lock_path = tmp_path / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")
    gitdir = tmp_path / ".git"
    gitdir.mkdir()

    fake_proc = tmp_path / "proc"
    fake_proc.mkdir()

    # Create 140 non-holder processes
    for p in range(1, 140):
        p_dir = fake_proc / str(p)
        p_dir.mkdir()
        (p_dir / "fd").mkdir()

    # Process 140 holds the lock open
    holder_dir = fake_proc / "140"
    holder_dir.mkdir()
    holder_fd = holder_dir / "fd"
    holder_fd.mkdir()
    (holder_fd / "3").symlink_to(str(lock_path.resolve()))

    def fake_run(
        cmd: object,
        *,
        timeout: float | None = None,
        capture_output: bool = False,
        check: bool = False,
    ) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout=b"")

    monkeypatch.setattr("ralph.git._lock_recovery.subprocess.run", fake_run)

    result = _lock_has_no_live_holder(lock_path, gitdir, _proc_root=fake_proc)
    assert result == "live", (
        "Process beyond the old 120-PID cap (PID 140) must be inspected and detected as live"
    )


def test_liveness_probe_unreadable_pid_returns_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unreadable PID (PermissionError) prevents negative liveness proof and returns 'unknown'."""
    lock_path = tmp_path / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")
    gitdir = tmp_path / ".git"
    gitdir.mkdir()

    fake_proc = tmp_path / "proc"
    fake_proc.mkdir()

    unreadable_dir = fake_proc / "100"
    unreadable_dir.mkdir()
    fd_dir = unreadable_dir / "fd"
    fd_dir.mkdir()
    fd_dir.chmod(0o000)

    def fake_run(
        cmd: object,
        *,
        timeout: float | None = None,
        capture_output: bool = False,
        check: bool = False,
    ) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout=b"")

    monkeypatch.setattr("ralph.git._lock_recovery.subprocess.run", fake_run)

    try:
        result = _lock_has_no_live_holder(lock_path, gitdir, _proc_root=fake_proc)
        assert result == "unknown", (
            "Unreadable candidate PID must return 'unknown' (fail-closed negative proof)"
        )
    finally:
        fd_dir.chmod(0o755)


def _fake_proc_with_unreadable_fd(tmp_path: Path, comm: str, cmdline: bytes) -> tuple[Path, Path]:
    fake_proc = tmp_path / "proc"
    fake_proc.mkdir()
    pid_dir = fake_proc / "77"
    pid_dir.mkdir()
    (pid_dir / "comm").write_text(f"{comm}\n", encoding="utf-8")
    (pid_dir / "cmdline").write_bytes(cmdline)
    fd_dir = pid_dir / "fd"
    fd_dir.mkdir()
    fd_dir.chmod(0o000)
    return fake_proc, fd_dir


def test_liveness_probe_unreadable_non_git_fd_does_not_veto_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-git process with an unreadable fd table cannot be a git lock holder.

    systemd/sshd privsep/sandboxed helpers are on every host; if they vetoed
    recovery, "dead" would be unprovable and an orphaned lock permanent.
    """
    gitdir = tmp_path / "repo" / ".git"
    gitdir.mkdir(parents=True)
    lock_path = gitdir / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")
    fake_proc, fd_dir = _fake_proc_with_unreadable_fd(
        tmp_path, "rsync", b"/usr/bin/rsync\x00-av\x00"
    )
    monkeypatch.setattr(
        "ralph.git._lock_recovery.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout=b""),
    )
    try:
        # A fresh lock may still be a git client's: unreadable stays unknown.
        assert _lock_has_no_live_holder(lock_path, gitdir, _proc_root=fake_proc) == "unknown"
        # Once the lock has outlived any git operation Ralph would wait on, a
        # proven non-git process with an unreadable fd table cannot be it.
        long_ago = time.time() - 10_000
        os.utime(lock_path, (long_ago, long_ago))
        assert _lock_has_no_live_holder(lock_path, gitdir, _proc_root=fake_proc) == "dead"
    finally:
        fd_dir.chmod(0o755)


def test_liveness_probe_unreadable_git_fd_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A git process whose state cannot be inspected keeps the lock (unknown)."""
    gitdir = tmp_path / "repo" / ".git"
    gitdir.mkdir(parents=True)
    lock_path = gitdir / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")
    fake_proc, fd_dir = _fake_proc_with_unreadable_fd(tmp_path, "git", b"/usr/bin/git\x00add\x00")
    monkeypatch.setattr(
        "ralph.git._lock_recovery.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout=b""),
    )
    try:
        assert _lock_has_no_live_holder(lock_path, gitdir, _proc_root=fake_proc) == "unknown"
    finally:
        fd_dir.chmod(0o755)


def test_liveness_probe_inaccessible_metadata_fails_closed(
    tmp_path: Path,
) -> None:
    """Inaccessible or corrupted metadata (commondir/gitdir) fails closed to 'unknown'."""
    repo_dir = tmp_path / "worktree"
    repo_dir.mkdir()
    gitdir = repo_dir / ".git"
    gitdir.mkdir()
    lock_path = gitdir / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")

    commondir_file = gitdir / "commondir"
    commondir_file.write_text("../../main/.git\n", encoding="utf-8")
    commondir_file.chmod(0o000)

    try:
        result = _lock_has_no_live_holder(lock_path, gitdir, _proc_root=tmp_path / "proc")
        assert result == "unknown", "Inaccessible commondir must fail closed to 'unknown'"
    finally:
        commondir_file.chmod(0o644)

    # Empty commondir also fails closed
    commondir_file.write_text("", encoding="utf-8")
    result = _lock_has_no_live_holder(lock_path, gitdir, _proc_root=tmp_path / "proc")
    assert result == "unknown", "Empty commondir metadata must fail closed to 'unknown'"


def test_liveness_probe_blocked_metadata_within_whole_probe_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blocked metadata read (e.g. stalled NFS) is bound by the 0.5s probe timeout."""
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    lock_path = gitdir / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")

    commondir_file = gitdir / "commondir"
    commondir_file.write_text("../common\n", encoding="utf-8")

    real_read_text = Path.read_text
    gate = threading.Event()

    def slow_read_text(self: Path, encoding: str | None = None, errors: str | None = None) -> str:
        if self.name == "commondir":
            gate.wait(timeout=2.0)
        return real_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", slow_read_text)

    try:
        result = _lock_has_no_live_holder(lock_path, gitdir, probe_timeout_s=0.05)
        assert result == "unknown", "Stalled metadata read must return 'unknown'"
    finally:
        gate.set()


def test_probe_worker_lifecycle_under_repeated_timeouts_no_accumulation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated probe timeouts do not accumulate stuck worker threads beyond pool limit."""
    lock_path = tmp_path / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")
    gitdir = tmp_path / ".git"
    gitdir.mkdir()

    gate = threading.Event()

    def hung_lsof(*args: object, **kwargs: object) -> bool:
        gate.wait(timeout=1.0)
        return False

    monkeypatch.setattr("ralph.git.operations._probe_lsof_fuser", hung_lsof)

    # Reset executor to clean slate
    _reset_probe_executor()

    try:
        # Execute 6 probe calls with timeouts
        for _ in range(6):
            res = _lock_has_no_live_holder(
                lock_path,
                gitdir,
                probe_timeout_s=0.05,
                _proc_root=tmp_path / "nonexistent",
            )
            assert res == "unknown", "Timed out probe must return 'unknown'"

        executor = _get_probe_executor()
        # Workers must not accumulate beyond the bounded pool size (_PROBE_MAX_WORKERS = 4)
        assert len(executor._threads) <= _PROBE_MAX_WORKERS, (
            f"Thread pool accumulated {len(executor._threads)} threads, exceeding cap {_PROBE_MAX_WORKERS}"
        )
    finally:
        gate.set()
        _reset_probe_executor()


def test_liveness_probe_zombie_process_treated_as_dead(
    tmp_path: Path,
) -> None:
    """Zombie/dead processes (/proc/<pid>/status State: Z) are definitively treated as dead."""
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    lock_path = gitdir / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")

    fake_proc = tmp_path / "proc"
    fake_proc.mkdir()
    pid_dir = fake_proc / "88"
    pid_dir.mkdir()
    (pid_dir / "comm").write_text("git\n", encoding="utf-8")
    (pid_dir / "status").write_text("Name:\tgit\nState:\tZ (zombie)\nPid:\t88\n", encoding="utf-8")
    fd_dir = pid_dir / "fd"
    fd_dir.mkdir()
    fd_dir.chmod(0o000)

    try:
        result = _lock_has_no_live_holder(lock_path, gitdir, _proc_root=fake_proc)
        assert result == "dead", (
            "Zombie process must be recognized as dead even with unreadable fd table"
        )
    finally:
        fd_dir.chmod(0o755)


def test_liveness_probe_cwd_in_repo_subdirectory_is_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A git process whose cwd is a subdirectory of the worktree top returns 'live'.

    The OR-predicate must accept any path inside the repository (worktree top,
    main-repo top, or any subdirectory of either) as a live holder signal. A
    developer running ``git status`` from a project subdirectory while a long
    ``git gc`` holds a worktree-private lock must be treated as a live holder;
    unlinking the lock would corrupt the gc.
    """
    # Real worktree layout: repo_root is the worktree top, .git/worktrees/wt-17
    # is the per-worktree gitdir, repo_root/.git is the common gitdir symlink.
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    worktree_dot_git = repo_root / ".git"
    worktree_dot_git.mkdir()
    common_gitdir = tmp_path / "common_git"
    common_gitdir.mkdir()
    worktree_gitdir = common_gitdir / "worktrees" / "wt-17"
    worktree_gitdir.mkdir(parents=True)
    (worktree_gitdir / "gitdir").write_text(str(worktree_dot_git) + "\n", encoding="utf-8")
    (worktree_gitdir / "commondir").write_text("../..\n", encoding="utf-8")

    lock_path = worktree_gitdir / "index.lock"
    lock_path.write_bytes(b"\x00\x01binary index contents\x00")

    subdir = repo_root / "src" / "pkg" / "deep"
    subdir.mkdir(parents=True)

    fake_proc = tmp_path / "proc"
    fake_proc.mkdir()
    pid_dir = fake_proc / str(os.getpid())
    pid_dir.mkdir()
    # cwd is a subdirectory of the worktree top — not an exact match for any
    # live_cwd entry. The expanded OR-predicate must still recognize it.
    (pid_dir / "cwd").symlink_to(str(subdir.resolve()))
    (pid_dir / "fd").mkdir()  # empty: no lock open
    (pid_dir / "comm").write_text("git\n", encoding="utf-8")
    (pid_dir / "environ").write_bytes(b"")  # no GIT_DIR

    def fake_run(
        cmd: object,
        *,
        timeout: float | None = None,
        capture_output: bool = False,
        check: bool = False,
    ) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout=b"")

    monkeypatch.setattr("ralph.git._lock_recovery.subprocess.run", fake_run)

    result = _lock_has_no_live_holder(
        lock_path, worktree_gitdir, common_gitdir, _proc_root=fake_proc
    )
    assert result == "live", (
        "Process with cwd in a subdirectory of the worktree top must return 'live'; "
        "the OR-predicate must recognize ANY path under the worktree top, not only "
        "the exact worktree-top path itself"
    )


def _make_fake_proc(root: Path, pid: str, comm: str = "git") -> Path:
    p = root / "proc" / pid
    p.mkdir(parents=True)
    (p / "comm").write_text(f"{comm}\n", encoding="utf-8")
    (p / "fd").mkdir()
    return p


def test_systemd_unreadable_descriptors_does_not_veto_recovery(tmp_path: Path) -> None:
    """systemd/init with unreadable descriptors is not git, so it cannot veto recovery."""
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    pid_dir = _make_fake_proc(tmp_path, "1", "systemd")
    (pid_dir / "fd").chmod(0o000)
    lock = gitdir / "index.lock"
    lock.write_text("stale", encoding="utf-8")
    long_ago = time.time() - 10_000
    os.utime(lock, (long_ago, long_ago))
    try:
        res = _lock_has_no_live_holder(lock, gitdir, _proc_root=tmp_path / "proc")
        assert res == "dead"
    finally:
        (pid_dir / "fd").chmod(0o755)


def test_relative_gitdir_outside_repo_resolves_live(tmp_path: Path) -> None:
    """relative GIT_DIR with cwd outside repo resolves to repo gitdir and returns live."""
    gitdir, outside = tmp_path / "repo" / ".git", tmp_path / "outside"
    gitdir.mkdir(parents=True)
    outside.mkdir()
    pid_dir = _make_fake_proc(tmp_path, "42")
    (pid_dir / "cwd").symlink_to(str(outside.resolve()))
    rel_gitdir = os.path.relpath(gitdir.resolve(), outside.resolve())
    (pid_dir / "environ").write_bytes(f"GIT_DIR={rel_gitdir}\x00".encode())
    res = _lock_has_no_live_holder(gitdir / "index.lock", gitdir, _proc_root=tmp_path / "proc")
    assert res == "live"


def test_relative_gitdir_outside_repo_not_pointing_to_gitdir_no_match(tmp_path: Path) -> None:
    """relative GIT_DIR that does not point to repo gitdir returns not_in_repo / no_match."""
    gitdir, outside = tmp_path / "repo" / ".git", tmp_path / "outside"
    gitdir.mkdir(parents=True)
    outside.mkdir()
    pid_dir = _make_fake_proc(tmp_path, "43")
    (pid_dir / "cwd").symlink_to(str(outside.resolve()))
    (pid_dir / "environ").write_bytes(b"GIT_DIR=../other/.git\x00")
    assert _pid_in_repo_cwd(pid_dir, {gitdir.resolve()}, gitdir.resolve() / "wt") == "not_in_repo"
    assert _pid_has_gitdir_environ(pid_dir, gitdir, gitdir) == "no_match"
    res = _lock_has_no_live_holder(gitdir / "index.lock", gitdir, _proc_root=tmp_path / "proc")
    assert res == "dead"


def test_relative_gitdir_resolution_failure_fails_closed_unknown(tmp_path: Path) -> None:
    """resolution failure on relative GIT_DIR fails closed to unknown."""
    gitdir = tmp_path / "repo" / ".git"
    gitdir.mkdir(parents=True)
    pid_dir = _make_fake_proc(tmp_path, "44")
    (pid_dir / "environ").write_bytes(b"GIT_DIR=relative/.git\x00")
    assert _pid_has_gitdir_environ(pid_dir, gitdir, gitdir) == "unknown"
    res = _lock_has_no_live_holder(gitdir / "index.lock", gitdir, _proc_root=tmp_path / "proc")
    assert res == "unknown"
