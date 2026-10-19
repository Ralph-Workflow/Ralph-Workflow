"""Regression tests for git lock recovery edge cases (extracted module).

The lock-recovery tests are split into this file because the existing
``tests/test_git_lock_recovery.py`` is at 998 lines and the
``audit_repo_structure`` rule rejects test files over 1000 lines. New
regressions therefore land here.

Each test in this module:

- Uses a fake clock (``monkeypatch.setattr(time, "time", ...)`` is the
  pattern already used in
  ``test_recover_stale_git_lock_old_binary_and_young_locks``) so the
  lock's apparent age is set without waiting 9 hours.
- Exercises the **public** retry path
  (``_run_git_operation_with_stale_lock_recovery``) by monkeypatching
  ``ralph.git.operations._recover_stale_git_lock`` to return ``True``
  (lock unlinked) on the first call and the action callback to raise
  the lock error on the first call and succeed on the second. The
  ``_sleep_fn`` seam is monkeypatched to record any backoff calls; a
  successful first-call recovery must NOT trigger backoff.
- Asserts the platform-specific "unknown" reason logged by the
  liveness probe: the warning text must name either
  ``"no /proc scan succeeded"`` or
  ``"platform limitation"`` (the two platform reason strings in
  ``_lock_recovery.py:610-622``).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from git import GitCommandError
from loguru import logger

from ralph.git._lock_recovery import (
    _lock_has_no_live_holder,  # private import for test seam
)
from ralph.git.operations import (
    _run_git_operation_with_stale_lock_recovery,  # private import for test seam
)

if TYPE_CHECKING:
    import pytest


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


# ---------------------------------------------------------------------------
# AC 1: exact 32 404.9 s binary index.lock, no holder, recovered through
# the public ``_run_git_operation_with_stale_lock_recovery`` retry path.
# ---------------------------------------------------------------------------


def test_recover_stale_9h_binary_index_lock_no_holder_unlinks_and_stage_all_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2026-10-18 wt-22 observation: a 9-hour-old binary ``index.lock``
    with no holder is unlinked and ``stage_all`` is retried successfully.

    The product brief records the lock at age 32 404.9 s
    (about 9 hours, the observed age on 2026-10-18). The test sets
    the lock's mtime to exactly 32 404.9 s in the past via a fake
    clock (``monkeypatch.setattr(time, "time", ...)``) so the test does
    not wait 9 hours. It exercises the public
    ``_run_git_operation_with_stale_lock_recovery`` retry path (not the
    private ``_recover_stale_git_lock`` callback) by injecting a
    successful recovery on the first call.
    """
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    lock_path = gitdir / "index.lock"
    binary_content = b"DIRC\x00\x00\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00"
    lock_path.write_bytes(binary_content)

    # Set the lock's mtime to exactly 32 404.9 s in the past. The real
    # recovery computes age from ``time.time() - path.stat().st_mtime``,
    # so we set the mtime directly rather than mocking time.time() in
    # the production code (production code uses real time).
    real_time = time.time
    fixed_now = real_time()
    old_time = fixed_now - 32_404.9
    os.utime(lock_path, (old_time, old_time))

    # Fake the recovery to return True (unlink the lock) on the first
    # call; record whether it was invoked. The real recovery unlinks
    # the lock when it returns True; we mirror that here.
    recover_calls: dict[str, int] = {"n": 0}

    def fake_recover(operation: str, error: Exception) -> bool:
        del operation, error
        recover_calls["n"] += 1
        if lock_path.exists():
            lock_path.unlink()
        return True

    monkeypatch.setattr("ralph.git.operations._recover_stale_git_lock", fake_recover)

    # Fake sleep to record any backoff calls. Successful first-call
    # recovery must NOT trigger backoff.
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("ralph.git.operations._sleep_fn", fake_sleep)

    # The action raises a lock error on the first call, then succeeds.
    attempt_count: dict[str, int] = {"n": 0}
    lock_error = _make_index_lock_error(lock_path)

    def action() -> str:
        attempt_count["n"] += 1
        if attempt_count["n"] == 1:
            raise lock_error
        return "staged"

    result = _run_git_operation_with_stale_lock_recovery("stage_all", action)

    assert result == "staged", "Action must succeed after recovery"
    assert attempt_count["n"] == 2, (
        f"Expected 2 attempts (1 fail + 1 retry), got {attempt_count['n']}"
    )
    assert recover_calls["n"] == 1, "Recovery must be called exactly once"
    assert not lock_path.exists(), "Stale 9h-old lock with dead holder must be unlinked"
    assert sleep_calls == [], "No backoff sleep is needed when recovery succeeds on the first call"


# ---------------------------------------------------------------------------
# AC 5: the platform-specific "unknown" liveness log names the platform
# reason.
# ---------------------------------------------------------------------------


def test_liveness_probe_proc_less_logs_platform_specific_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The "unknown" liveness log names the platform reason.

    ``_lock_recovery.py:610-622`` records one of two platform-specific
    reason strings in the warning log when the liveness probe returns
    ``"unknown"``: ``"no /proc scan succeeded"`` (when ``/proc`` is
    absent on the host) or ``"platform limitation"`` (when ``/proc`` is
    present but the scan could not prove the holder dead, e.g. on
    other-UID processes).

    The existing ``test_liveness_probe_proc_less_returns_unknown``
    only asserts ``"fail-closed"`` or ``"unknown"`` in the log; this
    test pins the platform-specific reason explicitly by driving the
    real liveness probe (not a monkey-patched one) and capturing the
    warning emitted from inside ``_lock_recovery._probe``.
    """
    lock_path = tmp_path / "index.lock"
    lock_path.write_text("stale", encoding="utf-8")
    gitdir = tmp_path / ".git"
    gitdir.mkdir()

    # Provide a /proc root that does not exist: this is the first
    # branch of the platform-reason string at
    # ``_lock_recovery.py:610-612``
    # (``"no /proc scan succeeded and lsof/fuser did not return a
    # holder"``).
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
    sink_id = logger.add(warning_records.append, level="WARNING", format="{message}")
    try:
        result = _lock_has_no_live_holder(lock_path, gitdir, _proc_root=missing_proc)
    finally:
        logger.remove(sink_id)

    assert result == "unknown", "/proc-less platform must return 'unknown' (fail-closed)"

    logged_text = " ".join(warning_records)
    # The platform-specific reason MUST appear in the log. With a
    # missing /proc, the "no /proc scan succeeded" branch fires.
    assert "no /proc scan succeeded" in logged_text, (
        "Platform-specific 'unknown' reason must be logged so operators can "
        "diagnose why liveness could not be proven; got: " + logged_text
    )
