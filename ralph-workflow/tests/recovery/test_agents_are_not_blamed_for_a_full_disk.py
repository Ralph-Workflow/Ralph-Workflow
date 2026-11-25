from __future__ import annotations

from pathlib import Path

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.run_loop import dispatch_with_storage_guard
from ralph.recovery.controller import RecoveryController
from ralph.recovery.storage_pressure import GIB, StorageGuard
from ralph.recovery.unavailability_reason import UnavailabilityReason

_TEMP = Path("/volumes/system/tmp")


class _Disk:
    def __init__(self) -> None:
        self.free = 50 * GIB

    def free_bytes(self, _path: Path) -> int:
        return self.free


def _guard(disk: _Disk, clock: FakeClock) -> StorageGuard:
    def wait(seconds: float) -> None:
        clock.advance(seconds)
        disk.free = 50 * GIB

    return StorageGuard(
        (_TEMP,), disk_free=disk.free_bytes, volume_of=lambda _p: "system", wait=wait, clock=clock
    )


def test_an_agent_failing_because_the_disk_filled_keeps_no_cooldown() -> None:
    controller = RecoveryController()
    disk = _Disk()
    outcomes = iter([7, 0])

    def dispatch() -> int:
        exit_code = next(outcomes)
        if exit_code:
            disk.free = 0
            controller.mark_agent_unavailable(
                "development", "agy/model", reason=UnavailabilityReason.DEAD_AGENT_NO_PROGRESS
            )
        return exit_code

    result = dispatch_with_storage_guard(
        dispatch,
        phase="development",
        guard=_guard(disk, FakeClock()),
        controller=controller,
        failed_route="failed_terminal",
    )

    assert result == 0
    assert controller.cooled_down_agents() == frozenset()
    assert controller.agent_eligibility("development", "agy/model").is_eligible


def test_cooldowns_earned_with_plenty_of_disk_space_are_kept() -> None:
    controller = RecoveryController()
    disk = _Disk()

    def dispatch() -> int:
        controller.mark_agent_unavailable(
            "development", "agy/model", reason=UnavailabilityReason.DEAD_AGENT_NO_PROGRESS
        )
        return 0

    dispatch_with_storage_guard(
        dispatch,
        phase="development",
        guard=_guard(disk, FakeClock()),
        controller=controller,
        failed_route="failed_terminal",
    )

    assert controller.cooled_down_agents() == frozenset({"agy/model"})


def test_without_a_guard_the_step_runs_exactly_once() -> None:
    calls: list[int] = []

    def dispatch() -> int:
        calls.append(1)
        return 3

    result = dispatch_with_storage_guard(
        dispatch,
        phase="development",
        guard=None,
        controller=RecoveryController(),
        failed_route="failed_terminal",
    )

    assert result == 3
    assert calls == [1]
