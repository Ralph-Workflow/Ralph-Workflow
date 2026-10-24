"""Contract tests for the injectable lease clock of the git command queue.

The queue's lease handshake reads the age of an existing lease and stamps
the payload of a new lease through ONE seam:
``ralph.git.git_command_queue._lease_clock``. Replacing the seam lets a test
place a lease at any age, so the reclaim path is exercised without waiting
for wall-clock time and without reading the production clock.

The contract under test:

* the seam exists on the queue module and the payload of a NEW lease is
  stamped from the injected clock;
* the reclaim path reads the lease AGE from the injected clock and stamps
  the replacement payload from the same clock (one time domain);
* without a replacement, the production clock is used -- the default
  behaviour is unchanged.
"""

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
    """Minimal repository stand-in exposing only the git dir."""

    def __init__(self, git_dir: Path) -> None:
        self.git_dir = str(git_dir)


def _frozen_clock(monotonic: float, wall_time: float) -> LeaseClock:
    """Return a lease clock frozen at the given readings."""
    return LeaseClock(monotonic=lambda: monotonic, wall_time=lambda: wall_time)


def _lease_payload(git_dir: Path) -> str:
    """Return the raw lease payload stored under ``git_dir``."""
    return (git_dir / _LEASE_RELATIVE_PATH).read_text(encoding="utf-8")


def test_new_lease_payload_is_stamped_from_the_injected_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The holder payload carries the injected monotonic reading.

    ``monkeypatch.setattr`` raises ``AttributeError`` when the seam is
    missing, so this test also pins that the seam stays on the queue
    module.
    """
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
    """A crashed holder's lease is reclaimed against the injected clock.

    The stale lock-file names a monotonic timestamp of ``0.0``; with the
    injected clock at ``10000.0`` the lease is ancient, and the probe --
    not the age -- authorises the steal. The replacement payload is
    stamped from the SAME clock, so the lease stays in one time domain.
    """
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
    """Without a replacement the queue stamps leases from the real clock."""
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
    """The public lease surface keeps its import path and its behaviour."""
    assert WorktreeLeaseUnavailable is WorktreeLeaseUnavailableError
    assert issubclass(WorktreeLeaseUnavailableError, RuntimeError)
    assert WorktreeLease is not None
    assert WorktreeLeaseHolder(name="agent-phase").name == "agent-phase"
