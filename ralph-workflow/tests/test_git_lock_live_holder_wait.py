"""A live git lock holder is waited on, not counted against the recovery budget."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import GitCommandError

from ralph.git import lock_recovery as lr

if TYPE_CHECKING:
    from pathlib import Path


def _lock_error(lock_path: Path) -> GitCommandError:
    return GitCommandError(
        ["git", "add", "-A"],
        128,
        stderr=f"fatal: Unable to create '{lock_path}': File exists.",
    )


def _run(lock_path: Path, *, still_held_polls: int, waitable: bool) -> tuple[str, list[float]]:
    """Run an op whose lock stays held for ``still_held_polls`` extra attempts."""
    attempts = {"n": 0}
    sleeps: list[float] = []
    # The short backoff consumes the first attempts; every later failure is one wait.
    fail_until = len(lr.LOCK_BACKOFF_S) + still_held_polls

    def action() -> str:
        attempts["n"] += 1
        if attempts["n"] <= fail_until:
            raise _lock_error(lock_path)
        return "ok"

    result = lr.run_git_operation_with_stale_lock_recovery(
        "stage_all",
        action,
        recover_fn=lambda _op, _exc: False,
        sleep_fn=sleeps.append,
        holder_fn=lambda _p: "PID 1",
        waitable_fn=lambda _p: waitable,
    )
    return result, sleeps


def test_live_holder_is_waited_on_past_the_short_backoff(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    lock = tmp_path / ".git" / "index.lock"
    lock.write_text("held")
    result, sleeps = _run(lock, still_held_polls=30, waitable=True)
    assert result == "ok"
    assert sleeps[len(lr.LOCK_BACKOFF_S) :] == [lr.LIVE_HOLDER_POLL_S] * 30


def test_unprovable_holder_is_not_waited_on(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    lock = tmp_path / ".git" / "index.lock"
    lock.write_text("held")
    with pytest.raises(GitCommandError):
        _run(lock, still_held_polls=30, waitable=False)


def test_live_holder_wait_is_bounded_by_the_holder_ceiling(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    lock = tmp_path / ".git" / "index.lock"
    lock.write_text("held")
    with pytest.raises(GitCommandError):
        _run(lock, still_held_polls=int(lr.LIVE_HOLDER_PATIENCE_S) + 50, waitable=True)
