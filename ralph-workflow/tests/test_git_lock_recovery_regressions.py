from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest
from git import GitCommandError
from loguru import logger

from ralph.git._lock_recovery import (
    _lock_has_no_live_holder,
    _recover_stale_git_lock,
    _run_git_operation_with_stale_lock_recovery,
)


def _fake_dead(*args: object, **kwargs: object) -> Literal["live", "dead", "unknown"]:
    return "dead"


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


def test_recover_stale_branch_ref_lock_without_live_holder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gitdir = tmp_path / ".git"
    lock_path = gitdir / "refs" / "heads" / "wt-30-e2e-testing.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("stale", encoding="utf-8")
    lock_mtime = lock_path.stat().st_mtime
    monkeypatch.setattr(time, "time", lambda: lock_mtime + 60.0)
    error = GitCommandError(
        ["git", "commit"],
        128,
        stderr=(
            "fatal: update_ref failed for ref 'refs/heads/wt-30-e2e-testing': "
            f"cannot lock ref: Unable to create '{lock_path}': File exists."
        ),
    )

    recovered = _recover_stale_git_lock("create_commit", error, liveness_fn=_fake_dead)

    assert recovered is True
    assert not lock_path.exists()


def test_external_probe_can_prove_stale_lock_dead_when_proc_probe_is_unavailable(
    tmp_path: Path,
) -> None:
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    lock_path = gitdir / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")

    result = _lock_has_no_live_holder(
        lock_path,
        gitdir,
        _proc_root=tmp_path / "missing-proc",
        probe_lsof_fuser_fn=lambda _lock, _timeout: "dead",
    )

    assert result == "dead"


def test_recover_stale_9h_binary_index_lock_no_holder_unlinks_and_stage_all_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    lock_path = gitdir / "index.lock"
    binary_content = b"DIRC\x00\x00\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00"
    lock_path.write_bytes(binary_content)

    exact_age_seconds = 32_404.9
    current_mtime = lock_path.stat().st_mtime
    clock_queries: list[float] = []

    def fake_time() -> float:
        now = current_mtime + exact_age_seconds
        clock_queries.append(now)
        return now

    monkeypatch.setattr(time, "time", fake_time)

    recover_calls: dict[str, int] = {"n": 0}
    observed_lock_ages: list[float] = []

    def production_recover(operation: str, error: Exception) -> bool:
        recover_calls["n"] += 1
        observed_lock_ages.append(time.time() - lock_path.stat().st_mtime)
        return _recover_stale_git_lock(operation, error, liveness_fn=_fake_dead)

    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    attempt_count: dict[str, int] = {"n": 0}
    lock_error = _make_index_lock_error(lock_path)

    def action() -> str:
        attempt_count["n"] += 1
        if attempt_count["n"] == 1:
            raise lock_error
        return "staged"

    result = _run_git_operation_with_stale_lock_recovery(
        "stage_all",
        action,
        recover_fn=production_recover,
        sleep_fn=fake_sleep,
    )

    assert result == "staged", "Action must succeed after recovery"
    assert attempt_count["n"] == 2, (
        f"Expected 2 attempts (1 fail + 1 retry), got {attempt_count['n']}"
    )
    assert recover_calls["n"] == 1, "Recovery must be called exactly once"
    assert len(observed_lock_ages) == 1, "Expected exactly one lock age check during recovery"
    assert pytest.approx(observed_lock_ages[0], abs=1e-3) == 32_404.9, (
        f"Expected lock age ~32404.9s, got {observed_lock_ages[0]}"
    )
    assert round(observed_lock_ages[0], 1) == 32_404.9, (
        f"Expected rounded lock age of 32404.9s, got {observed_lock_ages[0]}"
    )
    assert len(clock_queries) >= 1, "Fake clock must be queried during recovery"
    assert round(clock_queries[0] - current_mtime, 1) == 32_404.9, (
        f"Expected fake clock delta of 32404.9s, got {clock_queries[0] - current_mtime}"
    )
    assert not lock_path.exists(), "Stale 9h-old lock with dead holder must be unlinked"
    assert sleep_calls == [], "No backoff sleep is needed when recovery succeeds on the first call"


def test_liveness_probe_proc_less_logs_platform_specific_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")
    gitdir = tmp_path / ".git"
    gitdir.mkdir()

    missing_proc = tmp_path / "no_such_proc"

    real_access = os.access

    def fake_access(path: str | Path, mode: int) -> bool:
        if str(path).startswith(str(missing_proc)):
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
        del cmd, timeout, capture_output, check
        return SimpleNamespace(returncode=1, stdout=b"")

    monkeypatch.setattr("ralph.git._lock_recovery.subprocess.run", fake_run)

    warning_records: list[str] = []
    sink_id = logger.add(warning_records.append, level="DEBUG", format="{message}")
    try:
        result = _lock_has_no_live_holder(lock_path, gitdir, _proc_root=missing_proc)
    finally:
        logger.remove(sink_id)

    assert result == "unknown", "/proc-less platform must return 'unknown' (fail-closed)"

    logged_text = " ".join(warning_records)
    assert "no /proc scan succeeded" in logged_text, (
        "Platform-specific 'unknown' reason must be logged so operators can "
        "diagnose why liveness could not be proven; got: " + logged_text
    )
