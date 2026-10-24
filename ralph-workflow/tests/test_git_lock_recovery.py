"""Tests for git stale lock recovery and backoff contention handling.

Extracted from ``tests/test_git_operations.py`` to keep both modules under
the 1000-line repo-structure limit.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest
from git import GitCommandError
from loguru import logger

from ralph.git._lock_recovery import (
    _PROBE_MAX_WORKERS,
    _get_probe_executor,
    _pid_has_gitdir_environ,
    _pid_in_repo_cwd,
    _reset_probe_executor,
)
from ralph.git.operations import (
    _LOCK_BACKOFF_S,
    _lock_has_no_live_holder,
    _recover_stale_git_lock,
    _run_git_operation_with_stale_lock_recovery,
)


def _make_index_lock_error(lock_path: Path) -> GitCommandError:
    return GitCommandError(
        ["git", "add", "-A"],
        128,
        stderr=(
            f"fatal: Unable to create '{lock_path}': File exists.\n"
            "Another git process seems to be running in this repository, e.g.\n"
            "an editor opened by 'git commit'. Please make sure all processes\n"
            "are terminated then try again. If it still fails, a git process\n"
            "may have crashed in this repository earlier:\n"
            "remove the file manually to continue."
        ),
    )


def _fake_dead(*args: object, **kwargs: object) -> Literal["live", "dead", "unknown"]:
    return "dead"


def _fake_live(*args: object, **kwargs: object) -> Literal["live", "dead", "unknown"]:
    return "live"


def _fake_unknown(*args: object, **kwargs: object) -> Literal["live", "dead", "unknown"]:
    return "unknown"


def test_recover_stale_git_lock_fails_closed_when_young(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock younger than 10s is NOT unlinked even with dead holder (age gate)."""
    lock_path = tmp_path / "index.lock"
    lock_path.write_bytes(b"index content\x00")
    young_time = time.time() - 2
    os.utime(lock_path, (young_time, young_time))

    monkeypatch.setattr(
        "ralph.git.operations._lock_has_no_live_holder",
        _fake_dead,
    )
    error = _make_index_lock_error(lock_path)

    assert _recover_stale_git_lock("stage_all", error) is False, (
        "Young lock must NOT be auto-unlinked even when holder is dead"
    )
    assert lock_path.exists(), "Young lock must remain on disk"


def test_recover_stale_git_lock_fails_closed_when_holder_is_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 60s-old lock with live holder is NOT unlinked (liveness gate)."""
    lock_path = tmp_path / "index.lock"
    lock_path.write_bytes(b"index content\x00")
    stale_time = time.time() - 60
    os.utime(lock_path, (stale_time, stale_time))

    monkeypatch.setattr(
        "ralph.git.operations._lock_has_no_live_holder",
        _fake_live,
    )
    error = _make_index_lock_error(lock_path)

    assert _recover_stale_git_lock("stage_all", error) is False, (
        "Lock with live holder must NOT be auto-unlinked"
    )
    assert lock_path.exists(), "Lock with live holder must remain on disk"


def test_recover_stale_git_lock_recovers_when_holder_is_dead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 60s-old lock with dead holder IS unlinked (both age and liveness met)."""
    (tmp_path / ".git").mkdir()
    lock_path = tmp_path / ".git" / "index.lock"
    lock_path.write_bytes(b"binary index content\x00")
    stale_time = time.time() - 60
    os.utime(lock_path, (stale_time, stale_time))

    monkeypatch.setattr(
        "ralph.git.operations._lock_has_no_live_holder",
        _fake_dead,
    )
    error = _make_index_lock_error(lock_path)

    assert _recover_stale_git_lock("stage_all", error) is True, (
        "Old lock with dead holder must be unlinked"
    )
    assert not lock_path.exists(), "Stale lock must be removed"


def test_recover_stale_git_lock_returns_false_for_non_lock_error() -> None:
    """A non-lock GitCommandError does NOT trigger recovery."""
    error = GitCommandError(
        ["git", "status"],
        128,
        stderr="fatal: not a git repository: '.git'",
    )

    assert _recover_stale_git_lock("status", error) is False


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


def test_liveness_probe_non_git_process_in_cwd_does_not_block_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-git process (e.g. Ralph/python) running in repo cwd without open fd returns 'dead'."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    gitdir = repo_dir / ".git"
    gitdir.mkdir()
    lock_path = gitdir / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")

    fake_proc = tmp_path / "proc"
    fake_proc.mkdir()
    ralph_pid_dir = fake_proc / "42"
    ralph_pid_dir.mkdir()
    (ralph_pid_dir / "cwd").symlink_to(str(repo_dir.resolve()))  # cwd -> repo dir
    (ralph_pid_dir / "fd").mkdir()  # empty fd dir (no lock file open)
    (ralph_pid_dir / "comm").write_text("python3\n", encoding="utf-8")  # NOT git
    (ralph_pid_dir / "cmdline").write_bytes(b"/usr/bin/python3\x00-m\x00ralph\x00")
    (ralph_pid_dir / "environ").write_bytes(b"PATH=/bin\x00")

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
    assert result == "dead", (
        "Non-git process in repo cwd with no open fd must return 'dead' so orphan lock can be recovered"
    )

    # Also verify _recover_stale_git_lock removes the stale lock
    stale_time = time.time() - 60
    os.utime(lock_path, (stale_time, stale_time))

    monkeypatch.setattr(
        "ralph.git.operations._lock_has_no_live_holder",
        _fake_dead,
    )
    error = _make_index_lock_error(lock_path)
    assert _recover_stale_git_lock("stage_all", error) is True
    assert not lock_path.exists(), (
        "Stale lock must be unlinked when only non-git processes exist in cwd"
    )


def test_liveness_probe_proc_less_returns_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On /proc-less platform, probe returns 'unknown' and refuses to unlink with logged reason."""
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    lock_path = gitdir / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")

    unreadable_proc = tmp_path / "proc"
    unreadable_proc.mkdir()

    real_access = os.access

    def fake_access(path: str | Path, mode: int) -> bool:
        if str(path).startswith(str(unreadable_proc)):
            return False
        return real_access(path, mode)

    monkeypatch.setattr(os, "access", fake_access)

    def fake_run(
        cmd: object,
        *,
        timeout: float | None = None,
        capture_output: bool = False,
        check: bool = False,
    ) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout=b"")

    monkeypatch.setattr("ralph.git._lock_recovery.subprocess.run", fake_run)

    result = _lock_has_no_live_holder(lock_path, gitdir, _proc_root=unreadable_proc)
    assert result == "unknown", "/proc-less platform must return 'unknown' (fail-closed)"

    stale_time = time.time() - 60
    os.utime(lock_path, (stale_time, stale_time))

    monkeypatch.setattr(
        "ralph.git.operations._lock_has_no_live_holder",
        _fake_unknown,
    )
    error = _make_index_lock_error(lock_path)

    warning_records: list[str] = []
    sink_id = logger.add(warning_records.append, level="WARNING", format="{message}")
    try:
        recovered = _recover_stale_git_lock("stage_all", error)
    finally:
        logger.remove(sink_id)

    assert recovered is False, "Recovery must refuse on liveness=unknown"
    assert lock_path.exists(), "Lock file must remain on disk"
    logged_text = " ".join(warning_records)
    assert "fail-closed" in logged_text or "unknown" in logged_text, (
        "Logged message must record fail-closed reason"
    )


class TestLockBackoff:
    """Tests for bounded backoff in _run_git_operation_with_stale_lock_recovery.

    The operation retries with backoff (0.2s, 0.5s, 1.0s; total ≤ 1.7s; up to 3
    retry attempts) when a git lock is held by a live process. The _sleep_fn seam
    allows tests to avoid real delays.

    The backoff is triggered when _recover_stale_git_lock returns False
    (lock is held by a live process). To test this reliably without a real
    lock file, we mock _recover_stale_git_lock.
    """

    def test_backoff_sleeps_then_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """First attempt refused, second refused, third refused, fourth fails — GitCommandError re-raised.

        A fake _sleep_fn records the backoff sequence so we can verify the
        schedule without real time passing. _recover_stale_git_lock is mocked
        to always return False (live holder) so backoff is always triggered.
        Records delays (0.2, 0.5, 1.0).
        """
        sleep_durations: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleep_durations.append(seconds)

        def fake_recover(_op: str, _err: Exception) -> bool:
            return False

        monkeypatch.setattr("ralph.git.operations._sleep_fn", fake_sleep)
        monkeypatch.setattr("ralph.git.operations._recover_stale_git_lock", fake_recover)

        attempt_count = 0
        lock_error = GitCommandError(
            ["git", "add", "-A"],
            128,
            stderr="fatal: Unable to create '/tmp/repo/.git/index.lock': File exists.\n"
            "Another git process seems to be running in this repository",
        )

        def action() -> str:
            nonlocal attempt_count
            attempt_count += 1
            raise lock_error

        with pytest.raises(GitCommandError):
            _run_git_operation_with_stale_lock_recovery("stage_all", action)

        # 4 attempts total: 1 initial attempt + 3 retries
        assert attempt_count == 4, f"Expected 4 attempts, got {attempt_count}"
        assert sleep_durations == [0.2, 0.5, 1.0], (
            f"Expected backoff [0.2, 0.5, 1.0], got {sleep_durations}"
        )

    def test_backoff_eventually_succeeds_when_holder_clears(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Live holder cleared between attempt 1 and 2 — action succeeds.

        First attempt fails with a lock error; _recover_stale_git_lock returns
        False (live holder) on the first call so backoff sleeps 0.2s. On
        the second attempt the lock holder has cleared, action succeeds, and
        the operation completes without further backoff.
        """
        sleep_durations: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleep_durations.append(seconds)

        monkeypatch.setattr("ralph.git.operations._sleep_fn", fake_sleep)
        recover_calls = 0

        def fake_recover(operation: str, error: Exception) -> bool:
            nonlocal recover_calls
            recover_calls += 1
            return recover_calls > 1  # False on first, True on second

        monkeypatch.setattr("ralph.git.operations._recover_stale_git_lock", fake_recover)

        lock_error = GitCommandError(
            ["git", "add", "-A"],
            128,
            stderr="fatal: Unable to create '/tmp/repo/.git/index.lock': File exists.\n"
            "Another git process seems to be running in this repository",
        )

        attempt_count = 0

        def action() -> str:
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count == 1:
                raise lock_error
            return "success"

        result = _run_git_operation_with_stale_lock_recovery("stage_all", action)

        assert result == "success"
        assert attempt_count == 2, f"Expected 2 attempts, got {attempt_count}"
        assert sleep_durations == [0.2], f"Expected single backoff [0.2], got {sleep_durations}"

    def test_backoff_exhausted_raises_git_command_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exhausted backoff re-raises the original GitCommandError."""

        def fake_sleep(_seconds: float) -> None:
            pass

        def fake_recover(_op: str, _err: Exception) -> bool:
            return False

        monkeypatch.setattr("ralph.git.operations._sleep_fn", fake_sleep)
        monkeypatch.setattr("ralph.git.operations._recover_stale_git_lock", fake_recover)

        lock_error = GitCommandError(
            ["git", "commit"],
            128,
            stderr="fatal: Unable to create '/tmp/repo/.git/index.lock': File exists.\n",
        )

        def action() -> str:
            raise lock_error

        with pytest.raises(GitCommandError) as exc_info:
            _run_git_operation_with_stale_lock_recovery("create_commit", action)

        assert exc_info.value is lock_error

    def test_backoff_total_under_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Total backoff budget is ≤ 1.7s (sum of 0.2 + 0.5 + 1.0).

        The constant _LOCK_BACKOFF_S is (0.2, 0.5, 1.0). The test verifies
        the declared budget without relying on the implementation sum.
        _recover_stale_git_lock is mocked to always return False so every
        retry triggers a backoff.
        """
        sleep_durations: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleep_durations.append(seconds)

        def fake_recover(_op: str, _err: Exception) -> bool:
            return False

        monkeypatch.setattr("ralph.git.operations._sleep_fn", fake_sleep)
        monkeypatch.setattr("ralph.git.operations._recover_stale_git_lock", fake_recover)

        lock_error = GitCommandError(
            ["git", "add", "-A"],
            128,
            stderr="fatal: Unable to create '/tmp/repo/.git/index.lock': File exists.\n"
            "Another git process seems to be running in this repository",
        )

        def action() -> str:
            raise lock_error

        with pytest.raises(GitCommandError):
            _run_git_operation_with_stale_lock_recovery("stage_all", action)

        total_sleep = sum(sleep_durations)
        assert total_sleep <= 1.7, f"Total backoff {total_sleep}s exceeds 1.7s budget"
        assert len(sleep_durations) == len(_LOCK_BACKOFF_S), (
            f"Expected {len(_LOCK_BACKOFF_S)} sleeps (max retries), got {len(sleep_durations)}"
        )
        assert sleep_durations == [0.2, 0.5, 1.0], (
            f"Expected observable backoff schedule [0.2, 0.5, 1.0], got {sleep_durations}"
        )
        assert _LOCK_BACKOFF_S == (0.2, 0.5, 1.0)


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


def test_liveness_probe_live_and_absent_holders(
    tmp_path: Path,
) -> None:
    """Distinct proof paths for live holders (refuse) vs absent holders (recover)."""
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    lock_path = gitdir / "index.lock"
    lock_path.write_bytes(b"DIRC\x00\x00\x00\x02\x00\x00\x00\x00")

    fake_proc = tmp_path / "proc"
    fake_proc.mkdir()

    # Process 55 holds open fd to lock_path
    holder_dir = fake_proc / "55"
    holder_dir.mkdir()
    (holder_dir / "comm").write_text("editor\n", encoding="utf-8")
    (holder_dir / "cmdline").write_bytes(b"/usr/bin/editor\x00")
    holder_fd = holder_dir / "fd"
    holder_fd.mkdir()
    (holder_fd / "3").symlink_to(str(lock_path.resolve()))

    result = _lock_has_no_live_holder(lock_path, gitdir, _proc_root=fake_proc)
    assert result == "live", "Process with open lock fd must be detected as 'live'"

    stale_time = time.time() - 60
    os.utime(lock_path, (stale_time, stale_time))
    error = _make_index_lock_error(lock_path)

    # When live holder exists, recovery refuses
    assert _recover_stale_git_lock("stage_all", error, liveness_fn=_fake_live) is False
    assert lock_path.exists(), "Lock with live holder must not be unlinked"

    # Now absent holder: remove process 55's link to lock_path
    (holder_fd / "3").unlink()
    (holder_fd / "3").symlink_to("/dev/null")

    result_absent = _lock_has_no_live_holder(lock_path, gitdir, _proc_root=fake_proc)
    assert result_absent == "dead", "Process not holding lock fd must yield 'dead'"

    # When holder is absent and age is >10s, recovery succeeds
    assert _recover_stale_git_lock("stage_all", error, liveness_fn=_fake_dead) is True
    assert not lock_path.exists(), "Stale lock with absent holder must be unlinked"


def test_recover_stale_git_lock_old_binary_and_young_locks(
    tmp_path: Path,
) -> None:
    """Old binary lock unlinks when holder absent; young lock refuses even when absent."""
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    lock_path = gitdir / "index.lock"
    binary_content = b"DIRC\x00\x00\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00"

    # Case 1: Young binary lock (2s old)
    lock_path.write_bytes(binary_content)
    young_time = time.time() - 2.0
    os.utime(lock_path, (young_time, young_time))
    error = _make_index_lock_error(lock_path)

    recovered = _recover_stale_git_lock("stage_all", error, liveness_fn=_fake_dead)
    assert recovered is False, "Young binary lock must NOT be unlinked"
    assert lock_path.exists(), "Young binary lock must remain on disk"

    # Case 2: Old binary lock (60s old)
    old_time = time.time() - 60.0
    os.utime(lock_path, (old_time, old_time))

    recovered = _recover_stale_git_lock("stage_all", error, liveness_fn=_fake_dead)
    assert recovered is True, "Old binary lock must be recovered when holder is dead"
    assert not lock_path.exists(), "Old binary lock must be unlinked from disk"


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
