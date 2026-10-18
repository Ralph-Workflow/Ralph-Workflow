"""Unit tests for git lock recovery and contention retry."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest
from git import GitCommandError

from ralph.git._lock_recovery import (
    _LOCK_BACKOFF_S,
    _lock_has_no_live_holder,
    _recover_stale_git_lock,
    _run_git_operation_with_stale_lock_recovery,
)


def _make_index_lock_error(lock_path: Path) -> GitCommandError:
    return GitCommandError(
        ["git", "add", "-A"],
        128,
        stderr=f"fatal: Unable to create '{lock_path}': File exists.\n\n"
        "Another git process seems to be running in this repository",
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
        "ralph.git._lock_recovery._lock_has_no_live_holder",
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
        "ralph.git._lock_recovery._lock_has_no_live_holder",
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
    lock_path = tmp_path / "index.lock"
    lock_path.write_bytes(b"binary index content\x00")
    stale_time = time.time() - 60
    os.utime(lock_path, (stale_time, stale_time))

    monkeypatch.setattr(
        "ralph.git._lock_recovery._lock_has_no_live_holder",
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


def test_liveness_probe_proc_less_returns_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On /proc-less platform, probe returns 'unknown' and refuses to unlink with logged reason."""
    lock_path = tmp_path / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")
    gitdir = tmp_path / ".git"
    gitdir.mkdir()

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
        "ralph.git._lock_recovery._lock_has_no_live_holder",
        _fake_unknown,
    )
    error = _make_index_lock_error(lock_path)

    from loguru import logger

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

        monkeypatch.setattr("ralph.git._lock_recovery._sleep_fn", fake_sleep)
        monkeypatch.setattr("ralph.git._lock_recovery._recover_stale_git_lock", fake_recover)

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

        monkeypatch.setattr("ralph.git._lock_recovery._sleep_fn", fake_sleep)
        recover_calls = 0

        def fake_recover(operation: str, error: Exception) -> bool:
            nonlocal recover_calls
            recover_calls += 1
            return recover_calls > 1  # False on first, True on second

        monkeypatch.setattr("ralph.git._lock_recovery._recover_stale_git_lock", fake_recover)

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

        monkeypatch.setattr("ralph.git._lock_recovery._sleep_fn", fake_sleep)
        monkeypatch.setattr("ralph.git._lock_recovery._recover_stale_git_lock", fake_recover)

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

        monkeypatch.setattr("ralph.git._lock_recovery._sleep_fn", fake_sleep)
        monkeypatch.setattr("ralph.git._lock_recovery._recover_stale_git_lock", fake_recover)

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
