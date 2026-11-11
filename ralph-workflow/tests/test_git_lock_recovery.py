from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Literal

import pytest
from git import GitCommandError
from loguru import logger

from ralph.git._lock_recovery import (
    LOCK_BACKOFF_S,
    lock_has_no_live_holder,
    recover_stale_git_lock,
    run_git_operation_with_stale_lock_recovery,
)


def make_index_lock_error(lock_path: Path) -> GitCommandError:
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


def fake_dead(*args: object, **kwargs: object) -> Literal["live", "dead", "unknown"]:
    return "dead"


def fake_live(*args: object, **kwargs: object) -> Literal["live", "dead", "unknown"]:
    return "live"


def fake_unknown(*args: object, **kwargs: object) -> Literal["live", "dead", "unknown"]:
    return "unknown"


def test_recover_stale_git_lock_fails_closed_when_young(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "index.lock"
    lock_path.write_bytes(b"index content\x00")
    young_time = time.time() - 2
    os.utime(lock_path, (young_time, young_time))

    error = make_index_lock_error(lock_path)

    assert recover_stale_git_lock("stage_all", error, liveness_fn=fake_dead) is False, (
        "Young lock must NOT be auto-unlinked even when holder is dead"
    )
    assert lock_path.exists(), "Young lock must remain on disk"


def test_recover_stale_git_lock_fails_closed_when_holder_is_live(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "index.lock"
    lock_path.write_bytes(b"index content\x00")
    stale_time = time.time() - 60
    os.utime(lock_path, (stale_time, stale_time))

    error = make_index_lock_error(lock_path)

    assert recover_stale_git_lock("stage_all", error, liveness_fn=fake_live) is False, (
        "Lock with live holder must NOT be auto-unlinked"
    )
    assert lock_path.exists(), "Lock with live holder must remain on disk"


def test_recover_stale_git_lock_recovers_when_holder_is_dead(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    lock_path = tmp_path / ".git" / "index.lock"
    lock_path.write_bytes(b"binary index content\x00")
    stale_time = time.time() - 60
    os.utime(lock_path, (stale_time, stale_time))

    error = make_index_lock_error(lock_path)

    assert recover_stale_git_lock("stage_all", error, liveness_fn=fake_dead) is True, (
        "Old lock with dead holder must be unlinked"
    )
    assert not lock_path.exists(), "Stale lock must be removed"


def test_recover_stale_git_lock_returns_false_for_non_lock_error() -> None:
    error = GitCommandError(
        ["git", "status"],
        128,
        stderr="fatal: not a git repository: '.git'",
    )

    assert recover_stale_git_lock("status", error) is False


def test_liveness_probe_non_git_process_in_cwd_does_not_block_recovery(
    tmp_path: Path,
) -> None:
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
    (ralph_pid_dir / "cwd").symlink_to(str(repo_dir.resolve()))
    (ralph_pid_dir / "fd").mkdir()
    (ralph_pid_dir / "comm").write_text("python3\n", encoding="utf-8")
    (ralph_pid_dir / "cmdline").write_bytes(b"/usr/bin/python3\x00-m\x00ralph\x00")
    (ralph_pid_dir / "environ").write_bytes(b"PATH=/bin\x00")

    result = lock_has_no_live_holder(lock_path, gitdir, proc_root=fake_proc)
    assert result == "dead", (
        "Non-git process in repo cwd with no open fd must return 'dead' so orphan lock can be recovered"
    )

    stale_time = time.time() - 60
    os.utime(lock_path, (stale_time, stale_time))

    error = make_index_lock_error(lock_path)
    assert recover_stale_git_lock("stage_all", error, liveness_fn=fake_dead) is True
    assert not lock_path.exists(), (
        "Stale lock must be unlinked when only non-git processes exist in cwd"
    )


def test_liveness_probe_proc_less_returns_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    result = lock_has_no_live_holder(lock_path, gitdir, proc_root=unreadable_proc)
    assert result == "unknown", "/proc-less platform must return 'unknown' (fail-closed)"

    stale_time = time.time() - 60
    os.utime(lock_path, (stale_time, stale_time))

    error = make_index_lock_error(lock_path)

    warning_records: list[str] = []
    sink_id = logger.add(warning_records.append, level="WARNING", format="{message}")
    try:
        recovered = recover_stale_git_lock("stage_all", error, liveness_fn=fake_unknown)
    finally:
        logger.remove(sink_id)

    assert recovered is False, "Recovery must refuse on liveness=unknown"
    assert lock_path.exists(), "Lock file must remain on disk"
    logged_text = " ".join(warning_records)
    assert "fail-closed" in logged_text or "unknown" in logged_text, (
        "Logged message must record fail-closed reason"
    )


class TestLockBackoff:
    def test_backoff_sleeps_then_retries(self) -> None:
        sleep_durations: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleep_durations.append(seconds)

        def fake_recover(_op: str, _err: Exception) -> bool:
            return False

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
            run_git_operation_with_stale_lock_recovery(
                "stage_all",
                action,
                sleep_fn=fake_sleep,
                recover_fn=fake_recover,
            )

        assert attempt_count == 4, f"Expected 4 attempts, got {attempt_count}"
        assert sleep_durations == [0.2, 0.5, 1.0], (
            f"Expected backoff [0.2, 0.5, 1.0], got {sleep_durations}"
        )

    def test_backoff_eventually_succeeds_when_holder_clears(self) -> None:
        sleep_durations: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleep_durations.append(seconds)

        recover_calls = 0

        def fake_recover(operation: str, error: Exception) -> bool:
            nonlocal recover_calls
            recover_calls += 1
            return recover_calls > 1

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

        result = run_git_operation_with_stale_lock_recovery(
            "stage_all",
            action,
            sleep_fn=fake_sleep,
            recover_fn=fake_recover,
        )

        assert result == "success"
        assert attempt_count == 2, f"Expected 2 attempts, got {attempt_count}"
        assert sleep_durations == [0.2], f"Expected single backoff [0.2], got {sleep_durations}"

    def test_backoff_exhausted_raises_git_command_error(self) -> None:

        def fake_sleep(_seconds: float) -> None:
            pass

        def fake_recover(_op: str, _err: Exception) -> bool:
            return False

        lock_error = GitCommandError(
            ["git", "commit"],
            128,
            stderr="fatal: Unable to create '/tmp/repo/.git/index.lock': File exists.\n",
        )

        def action() -> str:
            raise lock_error

        with pytest.raises(GitCommandError) as exc_info:
            run_git_operation_with_stale_lock_recovery(
                "create_commit",
                action,
                sleep_fn=fake_sleep,
                recover_fn=fake_recover,
            )

        assert exc_info.value is lock_error

    def test_backoff_total_under_budget(self) -> None:
        sleep_durations: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleep_durations.append(seconds)

        def fake_recover(_op: str, _err: Exception) -> bool:
            return False

        lock_error = GitCommandError(
            ["git", "add", "-A"],
            128,
            stderr="fatal: Unable to create '/tmp/repo/.git/index.lock': File exists.\n"
            "Another git process seems to be running in this repository",
        )

        def action() -> str:
            raise lock_error

        with pytest.raises(GitCommandError):
            run_git_operation_with_stale_lock_recovery(
                "stage_all",
                action,
                sleep_fn=fake_sleep,
                recover_fn=fake_recover,
            )

        total_sleep = sum(sleep_durations)
        assert total_sleep <= 1.7, f"Total backoff {total_sleep}s exceeds 1.7s budget"
        assert len(sleep_durations) == len(LOCK_BACKOFF_S), (
            f"Expected {len(LOCK_BACKOFF_S)} sleeps (max retries), got {len(sleep_durations)}"
        )
        assert sleep_durations == [0.2, 0.5, 1.0], (
            f"Expected observable backoff schedule [0.2, 0.5, 1.0], got {sleep_durations}"
        )
        assert LOCK_BACKOFF_S == (0.2, 0.5, 1.0)


def test_liveness_probe_live_and_absent_holders(
    tmp_path: Path,
) -> None:
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    lock_path = gitdir / "index.lock"
    lock_path.write_bytes(b"DIRC\x00\x00\x00\x02\x00\x00\x00\x00")

    fake_proc = tmp_path / "proc"
    fake_proc.mkdir()

    holder_dir = fake_proc / "55"
    holder_dir.mkdir()
    (holder_dir / "comm").write_text("editor\n", encoding="utf-8")
    (holder_dir / "cmdline").write_bytes(b"/usr/bin/editor\x00")
    holder_fd = holder_dir / "fd"
    holder_fd.mkdir()
    (holder_fd / "3").symlink_to(str(lock_path.resolve()))

    result = lock_has_no_live_holder(lock_path, gitdir, proc_root=fake_proc)
    assert result == "live", "Process with open lock fd must be detected as 'live'"

    stale_time = time.time() - 60
    os.utime(lock_path, (stale_time, stale_time))
    error = make_index_lock_error(lock_path)

    assert recover_stale_git_lock("stage_all", error, liveness_fn=fake_live) is False
    assert lock_path.exists(), "Lock with live holder must not be unlinked"

    (holder_fd / "3").unlink()
    (holder_fd / "3").symlink_to("/dev/null")

    result_absent = lock_has_no_live_holder(lock_path, gitdir, proc_root=fake_proc)
    assert result_absent == "dead", "Process not holding lock fd must yield 'dead'"

    assert recover_stale_git_lock("stage_all", error, liveness_fn=fake_dead) is True
    assert not lock_path.exists(), "Stale lock with absent holder must be unlinked"


def test_recover_stale_git_lock_old_binary_and_young_locks(
    tmp_path: Path,
) -> None:
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    lock_path = gitdir / "index.lock"
    binary_content = b"DIRC\x00\x00\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00"

    lock_path.write_bytes(binary_content)
    young_time = time.time() - 2.0
    os.utime(lock_path, (young_time, young_time))
    error = make_index_lock_error(lock_path)

    recovered = recover_stale_git_lock("stage_all", error, liveness_fn=fake_dead)
    assert recovered is False, "Young binary lock must NOT be unlinked"
    assert lock_path.exists(), "Young binary lock must remain on disk"

    old_time = time.time() - 60.0
    os.utime(lock_path, (old_time, old_time))

    recovered = recover_stale_git_lock("stage_all", error, liveness_fn=fake_dead)
    assert recovered is True, "Old binary lock must be recovered when holder is dead"
    assert not lock_path.exists(), "Old binary lock must be unlinked from disk"
