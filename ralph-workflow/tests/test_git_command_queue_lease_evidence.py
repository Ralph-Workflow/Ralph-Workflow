"""Contract tests for the evidence-gated worktree-lease reclaim.

The rule under test: a lease is NEVER reclaimed because it is old. A steal
requires POSITIVE EVIDENCE that the recorded holder is dead, and the
evidence comes from the shared git lock liveness probe. A live holder and
an indeterminate probe both refuse the steal, and the refusal names the
reason.

The tests use a duck-typed repository stand-in (``git_dir`` only) so no
real git repository -- and no real git process -- is involved.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from ralph.git.git_command_queue import (
    LeaseLiveness,
    WorktreeLeaseHolder,
    WorktreeLeaseUnavailable,
    begin_agent_lease,
    decide_lease_steal,
    end_agent_lease,
    is_lease_held,
)

_PROBE_ATTRIBUTE = "ralph.git.git_command_queue._lease_holder_liveness_probe"
_STALE_SECONDS = 600.0


class _FakeRepo:
    """Minimal repository stand-in exposing only the git dir."""

    def __init__(self, git_dir: Path) -> None:
        self.git_dir = str(git_dir)


def _probe_returning(verdict: LeaseLiveness) -> Callable[[Path, int | None], LeaseLiveness]:
    """Return a liveness probe that always answers ``verdict``."""

    def _probe(_lock_path: Path, _holder_pid: int | None) -> LeaseLiveness:
        return verdict

    return _probe


def _lease_path(git_dir: Path) -> Path:
    return git_dir / "ralph" / "agent-lease.lock"


def _write_crashed_holder_lease(git_dir: Path) -> Path:
    """Write the lock-file a crashed holder leaves behind (no live fd)."""
    lock_path = _lease_path(git_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("crashed-agent|0.0|424242\n", encoding="utf-8")
    return lock_path


def test_steal_is_refused_when_the_holder_is_alive() -> None:
    """A live holder refuses the steal even when the lease is ancient."""
    decision = decide_lease_steal(
        age_seconds=10_000.0,
        liveness="live",
        stale_seconds=_STALE_SECONDS,
    )
    assert decision.allowed is False
    assert decision.reason == "holder_alive"


def test_steal_is_refused_when_liveness_is_indeterminate() -> None:
    """An indeterminate probe never authorises a steal."""
    decision = decide_lease_steal(
        age_seconds=10_000.0,
        liveness="unknown",
        stale_seconds=_STALE_SECONDS,
    )
    assert decision.allowed is False
    assert decision.reason == "liveness_indeterminate"


def test_steal_requires_positive_evidence_of_death() -> None:
    """Only a dead holder authorises the reclaim; age is secondary."""
    dead_and_stale = decide_lease_steal(
        age_seconds=10_000.0,
        liveness="dead",
        stale_seconds=_STALE_SECONDS,
    )
    assert dead_and_stale.allowed is True
    assert dead_and_stale.reason == "holder_dead_and_stale"

    dead_and_fresh = decide_lease_steal(
        age_seconds=1.0,
        liveness="dead",
        stale_seconds=_STALE_SECONDS,
    )
    assert dead_and_fresh.allowed is True
    assert dead_and_fresh.reason == "holder_dead"


def test_live_holder_lease_is_not_stolen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A contended acquisition refuses to steal a live holder's lease."""
    git_dir = tmp_path / "git-dir"
    repo = _FakeRepo(git_dir)
    lease = begin_agent_lease(repo, holder=WorktreeLeaseHolder(name="agent-phase"))
    try:
        monkeypatch.setattr(_PROBE_ATTRIBUTE, _probe_returning("live"))
        with pytest.raises(WorktreeLeaseUnavailable) as excinfo:
            begin_agent_lease(repo, holder=WorktreeLeaseHolder(name="catchup"))
        assert "holder_alive" in str(excinfo.value)
    finally:
        end_agent_lease(lease)


def test_indeterminate_holder_lease_is_not_stolen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An indeterminate probe refuses the steal and names the reason."""
    git_dir = tmp_path / "git-dir"
    repo = _FakeRepo(git_dir)
    _write_crashed_holder_lease(git_dir)
    monkeypatch.setattr(_PROBE_ATTRIBUTE, _probe_returning("unknown"))
    with pytest.raises(WorktreeLeaseUnavailable) as excinfo:
        begin_agent_lease(repo, holder=WorktreeLeaseHolder(name="catchup"))
    assert "liveness_indeterminate" in str(excinfo.value)


def test_dead_holder_lease_is_reclaimed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A crashed holder's lease is reclaimed once the probe proves it dead."""
    git_dir = tmp_path / "git-dir"
    repo = _FakeRepo(git_dir)
    lock_path = _write_crashed_holder_lease(git_dir)
    monkeypatch.setattr(_PROBE_ATTRIBUTE, _probe_returning("dead"))
    lease = begin_agent_lease(repo, holder=WorktreeLeaseHolder(name="catchup"))
    try:
        assert lock_path.read_text(encoding="utf-8").startswith("catchup|")
    finally:
        end_agent_lease(lease)


def test_real_probe_proves_a_vanished_holder_dead(tmp_path: Path) -> None:
    """The shared liveness probe answers "dead" for a holder that is gone.

    This drives the REAL probe (no seam) against a lock-file that names a
    PID that cannot exist, so the reclaim is authorised by the reused
    git lock liveness probe itself.
    """
    git_dir = tmp_path / "git-dir"
    repo = _FakeRepo(git_dir)
    lock_path = _lease_path(git_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("vanished-agent|0.0|4194303\n", encoding="utf-8")
    lease = begin_agent_lease(repo, holder=WorktreeLeaseHolder(name="catchup"))
    try:
        assert lock_path.read_text(encoding="utf-8").startswith("catchup|")
    finally:
        end_agent_lease(lease)


def test_is_lease_held_follows_the_probe_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lease counts as held unless the holder is provably dead."""
    git_dir = tmp_path / "git-dir"
    repo = _FakeRepo(git_dir)
    assert is_lease_held(repo) is False
    lease = begin_agent_lease(repo, holder=WorktreeLeaseHolder(name="agent-phase"))
    try:
        monkeypatch.setattr(_PROBE_ATTRIBUTE, _probe_returning("live"))
        assert is_lease_held(repo) is True
        monkeypatch.setattr(_PROBE_ATTRIBUTE, _probe_returning("unknown"))
        assert is_lease_held(repo) is True
        monkeypatch.setattr(_PROBE_ATTRIBUTE, _probe_returning("dead"))
        assert is_lease_held(repo) is False
    finally:
        end_agent_lease(lease)
