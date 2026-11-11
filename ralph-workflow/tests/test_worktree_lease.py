from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Repo

from ralph.git.git_command_queue import (
    WorktreeLeaseHolder,
    WorktreeLeaseUnavailable,
    begin_agent_lease,
    end_agent_lease,
    is_lease_held,
    try_acquire_lease,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture()
def repo(tmp_git_repo: Path) -> Iterator[Repo]:
    repo = Repo(tmp_git_repo)
    yield repo
    repo.close()


def test_second_acquirer_gets_not_acquired(repo: Repo) -> None:
    holder = WorktreeLeaseHolder(name="agent-phase")
    lease = begin_agent_lease(repo, holder=holder)
    try:
        assert is_lease_held(repo) is True
        assert try_acquire_lease(repo, holder=WorktreeLeaseHolder(name="catchup")) is None
        with pytest.raises(WorktreeLeaseUnavailable):
            begin_agent_lease(repo, holder=WorktreeLeaseHolder(name="other"))
    finally:
        end_agent_lease(lease)
    assert is_lease_held(repo) is False
    lease2 = begin_agent_lease(repo, holder=holder)
    end_agent_lease(lease2)


def test_end_agent_lease_idempotent(repo: Repo) -> None:
    lease = begin_agent_lease(repo, holder=WorktreeLeaseHolder(name="a"))
    end_agent_lease(lease)
    end_agent_lease(lease)
    assert is_lease_held(repo) is False


def test_lock_file_lives_under_git_dir_ralph(repo: Repo) -> None:
    lease = begin_agent_lease(repo, holder=WorktreeLeaseHolder(name="a"))
    try:
        expected = Path(repo.git_dir) / "ralph" / "agent-lease.lock"
        assert expected.exists()
    finally:
        end_agent_lease(lease)
    assert not (Path(repo.git_dir) / "ralph" / "agent-lease.lock").exists()


def test_adversarial_interleaving_check_then_acquire(repo: Repo) -> None:
    assert is_lease_held(repo) is False
    agent_lease = begin_agent_lease(repo, holder=WorktreeLeaseHolder(name="agent-phase"))
    try:
        catchup_lease = try_acquire_lease(repo, holder=WorktreeLeaseHolder(name="catchup"))
        assert catchup_lease is None
    finally:
        end_agent_lease(agent_lease)
