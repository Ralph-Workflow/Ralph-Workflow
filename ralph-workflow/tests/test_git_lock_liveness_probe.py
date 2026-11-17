from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest

from ralph.git.lock_recovery import (
    ProbeExecutorStatus,
    lock_has_no_live_holder,
    pid_has_gitdir_environ,
    pid_in_repo_cwd,
    probe_executor_status,
    probe_pool_capacity,
    reset_probe_executor,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "fake probe gates widened from 1-2s self-release to a 60s hang cap": (
        "the same observable is asserted: a stalled probe or metadata read must "
        "fail closed to 'unknown' within the production probe timeout. The fake "
        "worker previously self-released after 1-2s of real clock, racing the "
        "0.05s production probe timeout under host contention; it now never "
        "completes on its own, so only the production timeout bounds the test. "
        "The 60s bound is a hang cap for the audit's no-blocking-wait rule"
    ),
}


def test_liveness_probe_whole_timeout_0_5s(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    result = lock_has_no_live_holder(lock_path, gitdir, proc_root=tmp_path / "proc")
    assert result == "unknown", "Probe timeout must return 'unknown', not 'live' or 'dead'"


def test_aged_orphan_skips_cwd_scan_when_no_fd_holder(
    tmp_path: Path,
) -> None:
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    lock_path = gitdir / "index.lock"
    lock_path.write_text("orphan", encoding="utf-8")
    aged = time.time() - 5000.0
    os.utime(lock_path, (aged, aged))

    fake_proc = tmp_path / "proc"
    fake_proc.mkdir()
    busy = fake_proc / "77"
    busy.mkdir()
    (busy / "fd").mkdir()
    (busy / "comm").write_text("git\n", encoding="utf-8")
    (busy / "cmdline").write_bytes(b"git\x00status\x00")
    (busy / "cwd").symlink_to(str(tmp_path.resolve()))
    (busy / "environ").write_bytes(b"PATH=/bin\x00")
    (busy / "status").write_text(
        "Name:\tgit\nState:\tS\nUid:\t1000\t1000\t1000\t1000\n", encoding="utf-8"
    )

    result = lock_has_no_live_holder(lock_path, gitdir, proc_root=fake_proc)
    assert result == "dead", (
        "An orphan lock older than the unprovable ceiling with no open FD holder "
        "must classify as 'dead' without requiring a full cwd/git binding scan"
    )


def _populate_large_idle_proc(fake_proc: Path, count: int, *, fd_noise: Path) -> None:
    fake_proc.mkdir(parents=True, exist_ok=True)
    for pid in range(1, count + 1):
        pid_dir = fake_proc / str(pid)
        pid_dir.mkdir()
        fd_dir = pid_dir / "fd"
        fd_dir.mkdir()
        for fd_n in range(8):
            (fd_dir / str(fd_n)).symlink_to(str(fd_noise))
        (pid_dir / "comm").write_text("python3\n", encoding="utf-8")
        (pid_dir / "cmdline").write_bytes(b"/usr/bin/python3\x00")
        (pid_dir / "status").write_text(
            "Name:\tpython3\nState:\tS\nUid:\t1000\t1000\t1000\t1000\n", encoding="utf-8"
        )


def test_aged_orphan_large_proc_stays_dead_when_full_walk_would_miss_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    lock_path = gitdir / "index.lock"
    lock_path.write_text("orphan", encoding="utf-8")
    aged = time.time() - 5000.0
    os.utime(lock_path, (aged, aged))

    noise = tmp_path / "noise.txt"
    noise.write_text("x", encoding="utf-8")
    fake_proc = tmp_path / "proc"
    _populate_large_idle_proc(fake_proc, 400, fd_noise=noise)

    import ralph.git._lock_liveness as lock_liveness

    def budget_breaking_pid_check(*_a: object, **_k: object) -> Literal["dead"]:
        time.sleep(0.01)
        return "dead"

    monkeypatch.setattr(lock_liveness, "_check_pid_liveness", budget_breaking_pid_check)

    result = lock_has_no_live_holder(
        lock_path,
        gitdir,
        proc_root=fake_proc,
        probe_timeout_s=0.5,
        probe_lsof_fuser_fn=lambda *_a, **_k: "unknown",
    )
    assert result == "dead", (
        "Aged orphan with no FD holder must classify 'dead' from a bounded FD-only "
        "pass even when a full per-PID liveness walk of a large /proc would exceed "
        "the 0.5s probe budget"
    )


def test_live_fd_holder_found_in_large_proc_before_full_walk_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    lock_path = gitdir / "index.lock"
    lock_path.write_text("held", encoding="utf-8")

    noise = tmp_path / "noise.txt"
    noise.write_text("x", encoding="utf-8")
    fake_proc = tmp_path / "proc"
    _populate_large_idle_proc(fake_proc, 400, fd_noise=noise)

    holder = fake_proc / "401"
    holder.mkdir()
    holder_fd = holder / "fd"
    holder_fd.mkdir()
    (holder_fd / "3").symlink_to(str(lock_path.resolve()))
    (holder / "comm").write_text("python3\n", encoding="utf-8")
    (holder / "status").write_text(
        "Name:\tpython3\nState:\tS\nUid:\t1000\t1000\t1000\t1000\n", encoding="utf-8"
    )

    import ralph.git._lock_liveness as lock_liveness

    def budget_breaking_pid_check(*_a: object, **_k: object) -> Literal["dead"]:
        time.sleep(0.01)
        return "dead"

    monkeypatch.setattr(lock_liveness, "_check_pid_liveness", budget_breaking_pid_check)

    result = lock_has_no_live_holder(
        lock_path,
        gitdir,
        proc_root=fake_proc,
        probe_timeout_s=0.5,
        probe_lsof_fuser_fn=lambda *_a, **_k: "unknown",
    )
    assert result == "live", (
        "A live open-FD holder must be discovered by the FD-first pass among a large "
        "/proc before any full per-PID walk can exhaust the 0.5s probe budget"
    )


def test_proven_dead_proc_scan_is_not_discarded_by_slow_external_probe(
    tmp_path: Path,
) -> None:
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    lock_path = gitdir / "index.lock"
    lock_path.write_text("orphan", encoding="utf-8")

    fake_proc = tmp_path / "proc"
    fake_proc.mkdir()
    idle = fake_proc / "99"
    idle.mkdir()
    (idle / "fd").mkdir()
    (idle / "comm").write_text("python3\n", encoding="utf-8")
    (idle / "cmdline").write_bytes(b"/usr/bin/python3\x00")
    (idle / "cwd").symlink_to(str(tmp_path.resolve()))
    (idle / "environ").write_bytes(b"PATH=/bin\x00")
    (idle / "status").write_text(
        "Name:\tpython3\nState:\tS\nUid:\t1000\t1000\t1000\t1000\n", encoding="utf-8"
    )

    gate = threading.Event()
    external_calls = {"count": 0}

    def hung_external(_lock: Path, _timeout: float) -> Literal["live", "dead", "unknown"]:
        external_calls["count"] += 1
        gate.wait(timeout=60)
        return "unknown"

    try:
        result = lock_has_no_live_holder(
            lock_path,
            gitdir,
            proc_root=fake_proc,
            probe_timeout_s=0.5,
            probe_lsof_fuser_fn=hung_external,
        )
        assert result == "dead", (
            "A completed /proc scan that proved no live holder must return 'dead' "
            "even when the secondary lsof/fuser probe would exhaust the budget"
        )
        assert external_calls["count"] == 0, (
            "External lsof/fuser must not run after /proc already proved 'dead'"
        )
    finally:
        gate.set()


def test_liveness_probe_or_predicate_cwd_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    gitdir_common = tmp_path / ".git"

    fake_proc = tmp_path / "proc"
    fake_proc.mkdir()
    fake_pid_dir = fake_proc / str(os.getpid())
    fake_pid_dir.mkdir()
    (fake_pid_dir / "cwd").symlink_to(str(gitdir.resolve()))
    (fake_pid_dir / "fd").mkdir()
    (fake_pid_dir / "comm").write_text("git\n", encoding="utf-8")
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

    result = lock_has_no_live_holder(lock_path, gitdir, gitdir_common, proc_root=fake_proc)
    assert result == "live", "Process with cwd=gitdir must return 'live' (OR predicate)"


def test_liveness_probe_common_gitdir_holder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    (fake_pid_dir / "cwd").symlink_to(str(gitdir_common.resolve()))
    (fake_pid_dir / "fd").mkdir()
    (fake_pid_dir / "comm").write_text("git\n", encoding="utf-8")
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

    result = lock_has_no_live_holder(lock_path, gitdir, gitdir_common, proc_root=fake_proc)
    assert result == "live", "Process with cwd=common-gitdir must return 'live'"


def test_liveness_probe_real_blocked_worker_elapsed_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")
    gitdir = tmp_path / ".git"
    gitdir.mkdir()

    gate = threading.Event()

    def hung_probe(*args: object, **kwargs: object) -> bool:
        gate.wait(timeout=60)
        return False

    monkeypatch.setattr("ralph.git.operations._probe_lsof_fuser", hung_probe)

    try:
        result = lock_has_no_live_holder(
            lock_path, gitdir, probe_timeout_s=0.05, proc_root=tmp_path / "proc"
        )
        assert result == "unknown", "Hung probe must fail-closed to 'unknown'"
    finally:
        gate.set()


def test_liveness_probe_holder_beyond_120_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")
    gitdir = tmp_path / ".git"
    gitdir.mkdir()

    fake_proc = tmp_path / "proc"
    fake_proc.mkdir()

    for p in range(1, 140):
        p_dir = fake_proc / str(p)
        p_dir.mkdir()
        (p_dir / "fd").mkdir()

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

    result = lock_has_no_live_holder(lock_path, gitdir, proc_root=fake_proc)
    assert result == "live", (
        "Process beyond the old 120-PID cap (PID 140) must be inspected and detected as live"
    )


def test_liveness_probe_unreadable_pid_returns_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        result = lock_has_no_live_holder(lock_path, gitdir, proc_root=fake_proc)
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
        assert lock_has_no_live_holder(lock_path, gitdir, proc_root=fake_proc) == "unknown"
        long_ago = time.time() - 10_000
        os.utime(lock_path, (long_ago, long_ago))
        assert lock_has_no_live_holder(lock_path, gitdir, proc_root=fake_proc) == "dead"
    finally:
        fd_dir.chmod(0o755)


def test_liveness_probe_unreadable_git_fd_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        assert lock_has_no_live_holder(lock_path, gitdir, proc_root=fake_proc) == "unknown"
    finally:
        fd_dir.chmod(0o755)


def test_liveness_probe_inaccessible_metadata_fails_closed(
    tmp_path: Path,
) -> None:
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
        result = lock_has_no_live_holder(lock_path, gitdir, proc_root=tmp_path / "proc")
        assert result == "unknown", "Inaccessible commondir must fail closed to 'unknown'"
    finally:
        commondir_file.chmod(0o644)

    commondir_file.write_text("", encoding="utf-8")
    result = lock_has_no_live_holder(lock_path, gitdir, proc_root=tmp_path / "proc")
    assert result == "unknown", "Empty commondir metadata must fail closed to 'unknown'"


def test_liveness_probe_blocked_metadata_within_whole_probe_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            gate.wait(timeout=60)
        return real_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", slow_read_text)

    try:
        result = lock_has_no_live_holder(lock_path, gitdir, probe_timeout_s=0.05)
        assert result == "unknown", "Stalled metadata read must return 'unknown'"
    finally:
        gate.set()


def test_probe_worker_lifecycle_under_repeated_timeouts_no_accumulation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")
    gitdir = tmp_path / ".git"
    gitdir.mkdir()

    gate = threading.Event()

    def hung_lsof(*args: object, **kwargs: object) -> bool:
        gate.wait(timeout=60)
        return False

    monkeypatch.setattr("ralph.git.operations._probe_lsof_fuser", hung_lsof)

    reset_probe_executor()

    try:
        for _ in range(6):
            res = lock_has_no_live_holder(
                lock_path,
                gitdir,
                probe_timeout_s=0.05,
                proc_root=tmp_path / "nonexistent",
            )
            assert res == "unknown", "Timed out probe must return 'unknown'"

        status = probe_executor_status()
        assert status.worker_count <= probe_pool_capacity(), (
            f"Thread pool accumulated {status.worker_count} threads, exceeding cap {probe_pool_capacity()}"
        )
        assert status.healthy, f"Probe executor status unhealthy: {status}"
        assert status.is_initialized
        assert status.capacity == probe_pool_capacity()
    finally:
        gate.set()
        reset_probe_executor()


def test_probe_executor_status_inspection() -> None:
    reset_probe_executor()
    try:
        initial = probe_executor_status()
        assert isinstance(initial, ProbeExecutorStatus)
        assert initial.capacity == probe_pool_capacity()
        assert initial.worker_count == 0
        assert not initial.is_initialized
        assert initial.healthy
    finally:
        reset_probe_executor()


def test_liveness_probe_zombie_process_treated_as_dead(
    tmp_path: Path,
) -> None:
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
        result = lock_has_no_live_holder(lock_path, gitdir, proc_root=fake_proc)
        assert result == "dead", (
            "Zombie process must be recognized as dead even with unreadable fd table"
        )
    finally:
        fd_dir.chmod(0o755)


def test_liveness_probe_cwd_in_repo_subdirectory_is_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    (pid_dir / "cwd").symlink_to(str(subdir.resolve()))
    (pid_dir / "fd").mkdir()
    (pid_dir / "comm").write_text("git\n", encoding="utf-8")
    (pid_dir / "environ").write_bytes(b"")

    def fake_run(
        cmd: object,
        *,
        timeout: float | None = None,
        capture_output: bool = False,
        check: bool = False,
    ) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout=b"")

    monkeypatch.setattr("ralph.git._lock_recovery.subprocess.run", fake_run)

    result = lock_has_no_live_holder(lock_path, worktree_gitdir, common_gitdir, proc_root=fake_proc)
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
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    pid_dir = _make_fake_proc(tmp_path, "1", "systemd")
    (pid_dir / "fd").chmod(0o000)
    lock = gitdir / "index.lock"
    lock.write_text("stale", encoding="utf-8")
    long_ago = time.time() - 10_000
    os.utime(lock, (long_ago, long_ago))
    try:
        res = lock_has_no_live_holder(lock, gitdir, proc_root=tmp_path / "proc")
        assert res == "dead"
    finally:
        (pid_dir / "fd").chmod(0o755)


def test_relative_gitdir_outside_repo_resolves_live(tmp_path: Path) -> None:
    gitdir, outside = tmp_path / "repo" / ".git", tmp_path / "outside"
    gitdir.mkdir(parents=True)
    outside.mkdir()
    pid_dir = _make_fake_proc(tmp_path, "42")
    (pid_dir / "cwd").symlink_to(str(outside.resolve()))
    rel_gitdir = os.path.relpath(gitdir.resolve(), outside.resolve())
    (pid_dir / "environ").write_bytes(f"GIT_DIR={rel_gitdir}\x00".encode())
    res = lock_has_no_live_holder(gitdir / "index.lock", gitdir, proc_root=tmp_path / "proc")
    assert res == "live"


def test_relative_gitdir_outside_repo_not_pointing_to_gitdir_no_match(tmp_path: Path) -> None:
    gitdir, outside = tmp_path / "repo" / ".git", tmp_path / "outside"
    gitdir.mkdir(parents=True)
    outside.mkdir()
    pid_dir = _make_fake_proc(tmp_path, "43")
    (pid_dir / "cwd").symlink_to(str(outside.resolve()))
    (pid_dir / "environ").write_bytes(b"GIT_DIR=../other/.git\x00")
    assert pid_in_repo_cwd(pid_dir, {gitdir.resolve()}, gitdir.resolve() / "wt") == "not_in_repo"
    assert pid_has_gitdir_environ(pid_dir, gitdir, gitdir) == "no_match"
    res = lock_has_no_live_holder(gitdir / "index.lock", gitdir, proc_root=tmp_path / "proc")
    assert res == "dead"


def test_relative_gitdir_resolution_failure_fails_closed_unknown(tmp_path: Path) -> None:
    gitdir = tmp_path / "repo" / ".git"
    gitdir.mkdir(parents=True)
    pid_dir = _make_fake_proc(tmp_path, "44")
    (pid_dir / "environ").write_bytes(b"GIT_DIR=relative/.git\x00")
    assert pid_has_gitdir_environ(pid_dir, gitdir, gitdir) == "unknown"
    res = lock_has_no_live_holder(gitdir / "index.lock", gitdir, proc_root=tmp_path / "proc")
    assert res == "unknown"
