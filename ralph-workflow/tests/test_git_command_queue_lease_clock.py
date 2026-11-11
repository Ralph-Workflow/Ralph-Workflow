from __future__ import annotations

from pathlib import Path

import pytest

from ralph.git._worktree_lease import LeaseClock
from ralph.git.git_command_queue import (
    WorktreeLease,
    WorktreeLeaseHolder,
    WorktreeLeaseUnavailable,
    WorktreeLeaseUnavailableError,
    begin_agent_lease,
    end_agent_lease,
)

_CLOCK_ATTRIBUTE = "ralph.git.git_command_queue._lease_clock"
_PROBE_ATTRIBUTE = "ralph.git.git_command_queue._lease_holder_liveness_probe"
_LEASE_RELATIVE_PATH = Path("ralph") / "agent-lease.lock"


class _FakeRepo:
    def __init__(self, git_dir: Path) -> None:
        self.git_dir = str(git_dir)


def _frozen_clock(monotonic: float, wall_time: float) -> LeaseClock:
    return LeaseClock(monotonic=lambda: monotonic, wall_time=lambda: wall_time)


def _lease_payload(git_dir: Path) -> str:
    return (git_dir / _LEASE_RELATIVE_PATH).read_text(encoding="utf-8")


def test_new_lease_payload_is_stamped_from_the_injected_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git_dir = tmp_path / "git-dir"
    monkeypatch.setattr(_CLOCK_ATTRIBUTE, _frozen_clock(1234.5, 4321.0))

    lease = begin_agent_lease(_FakeRepo(git_dir), holder=WorktreeLeaseHolder(name="agent-phase"))
    try:
        payload = _lease_payload(git_dir)
    finally:
        end_agent_lease(lease)

    assert payload.startswith("agent-phase|1234.500000|")


def test_reclaim_reads_the_age_and_stamps_the_payload_from_the_injected_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git_dir = tmp_path / "git-dir"
    lock_path = git_dir / _LEASE_RELATIVE_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("crashed-agent|0.000000|424242\n", encoding="utf-8")
    monkeypatch.setattr(_CLOCK_ATTRIBUTE, _frozen_clock(10_000.0, 20_000.0))
    monkeypatch.setattr(_PROBE_ATTRIBUTE, lambda _lock_path, _holder_pid: "dead")

    lease = begin_agent_lease(_FakeRepo(git_dir), holder=WorktreeLeaseHolder(name="catchup"))
    try:
        payload = _lease_payload(git_dir)
    finally:
        end_agent_lease(lease)

    assert payload.startswith("catchup|10000.000000|")


def test_default_seam_stamps_the_lease_from_the_production_clock(tmp_path: Path) -> None:
    git_dir = tmp_path / "git-dir"
    lease = begin_agent_lease(_FakeRepo(git_dir), holder=WorktreeLeaseHolder(name="agent-phase"))
    try:
        payload = _lease_payload(git_dir)
    finally:
        end_agent_lease(lease)

    holder, timestamp, _pid = payload.strip().split("|")
    assert holder == "agent-phase"
    assert float(timestamp) > 0.0


def test_lease_types_stay_importable_from_the_queue_module() -> None:
    assert WorktreeLeaseUnavailable is WorktreeLeaseUnavailableError
    assert issubclass(WorktreeLeaseUnavailableError, RuntimeError)
    assert WorktreeLease is not None
    assert WorktreeLeaseHolder(name="agent-phase").name == "agent-phase"
