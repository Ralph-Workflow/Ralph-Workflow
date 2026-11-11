from __future__ import annotations

import fcntl
import os
from pathlib import Path

import pytest

from ralph.git.git_command_queue import (
    WorktreeLease,
    WorktreeLeaseHolder,
    begin_agent_lease,
    end_agent_lease,
    try_acquire_lease,
)

_VANISHED_PID = 4194303


class _FakeRepo:
    def __init__(self, git_dir: Path) -> None:
        self.git_dir = str(git_dir)


def _lease_path(git_dir: Path) -> Path:
    return git_dir / "ralph" / "agent-lease.lock"


def _identity(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino


def _fd_identity(fd: int) -> tuple[int, int] | None:
    try:
        stat = os.fstat(fd)
    except OSError:
        return None
    return stat.st_dev, stat.st_ino


def test_release_unlinks_the_lock_file_before_dropping_the_flock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git_dir = tmp_path / "git-dir"
    lease = begin_agent_lease(_FakeRepo(git_dir), holder=WorktreeLeaseHolder(name="a"))
    lock_path = _lease_path(git_dir)
    lease_identity = _identity(lock_path)
    linked_when_closed: list[bool] = []
    real_close = os.close

    def _observing_close(fd: int) -> None:
        if _fd_identity(fd) == lease_identity:
            linked_when_closed.append(lock_path.exists() and _identity(lock_path) == lease_identity)
        real_close(fd)

    monkeypatch.setattr(os, "close", _observing_close)
    end_agent_lease(lease)
    monkeypatch.undo()

    assert linked_when_closed == [False]
    assert not lock_path.exists()


def test_takeover_refuses_a_lock_on_an_inode_no_longer_at_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git_dir = tmp_path / "git-dir"
    repo = _FakeRepo(git_dir)
    lock_path = _lease_path(git_dir)
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(f"crashed-agent|0.0|{_VANISHED_PID}\n", encoding="utf-8")
    crashed_identity = _identity(lock_path)
    rival: list[WorktreeLease] = []
    real_flock = fcntl.flock

    def _interleaving_flock(fd: int, operation: int) -> None:
        if not rival and _fd_identity(fd) == crashed_identity:
            lock_path.unlink()
            rival.append(begin_agent_lease(repo, holder=WorktreeLeaseHolder(name="rival")))
        real_flock(fd, operation)

    monkeypatch.setattr(fcntl, "flock", _interleaving_flock)
    contender = try_acquire_lease(repo, holder=WorktreeLeaseHolder(name="contender"))
    monkeypatch.undo()

    try:
        assert rival, "the interleaving never ran: the takeover did not lock the old file"
        assert contender is None
        assert lock_path.read_text(encoding="utf-8").startswith("rival|")
    finally:
        if contender is not None:
            end_agent_lease(contender)
        for lease in rival:
            end_agent_lease(lease)


def test_main_checkout_and_linked_worktree_share_one_lease(tmp_path: Path) -> None:
    common = tmp_path / "main" / ".git"
    private = common / "worktrees" / "wt"
    private.mkdir(parents=True)
    (private / "commondir").write_text("../..\n", encoding="utf-8")

    lease = begin_agent_lease(_FakeRepo(private), holder=WorktreeLeaseHolder(name="bg"))
    try:
        assert lease.lock_path.resolve() == _lease_path(common).resolve()
        assert not _lease_path(private).exists()
        main = WorktreeLeaseHolder(name="main-agent")
        assert try_acquire_lease(_FakeRepo(common), holder=main) is None
    finally:
        end_agent_lease(lease)
