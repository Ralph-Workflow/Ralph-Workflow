from __future__ import annotations

import errno
from pathlib import Path

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.storage_pressure import (
    GIB,
    MAX_UNCONFIRMED_OUT_OF_SPACE_RETRIES,
    StorageGuard,
    is_out_of_space,
    run_step_with_storage_guard,
)

_TEMP = Path("/volumes/system/tmp")
_HOME = Path("/volumes/system/home")
_WORKSPACE = Path("/volumes/external/project")


class _Disk:
    def __init__(self, free: dict[Path, int]) -> None:
        self.free = dict(free)

    def free_bytes(self, path: Path) -> int:
        return self.free[path]

    @staticmethod
    def volume_of(path: Path) -> str:
        return path.parts[2]


def _guard(disk: _Disk, clock: FakeClock, *, on_wait: object = None) -> StorageGuard:
    def wait(seconds: float) -> None:
        clock.advance(seconds)
        if callable(on_wait):
            on_wait()

    return StorageGuard(
        (_TEMP, _HOME, _WORKSPACE),
        disk_free=disk.free_bytes,
        volume_of=disk.volume_of,
        wait=wait,
        clock=clock,
    )


def test_a_volume_below_the_floor_is_reported_once_per_volume() -> None:
    disk = _Disk({_TEMP: GIB // 2, _HOME: GIB // 2, _WORKSPACE: 400 * GIB})

    shortfalls = _guard(disk, FakeClock()).shortfalls()

    assert [shortfall.path for shortfall in shortfalls] == [_TEMP]
    assert "0.5 GiB free" in shortfalls[0].describe()


def test_plenty_of_space_never_pauses() -> None:
    disk = _Disk({_TEMP: 50 * GIB, _HOME: 50 * GIB, _WORKSPACE: 400 * GIB})
    clock = FakeClock()

    assert _guard(disk, clock).wait_until_clear() == 0.0
    assert clock.monotonic() == 0.0


def test_low_space_pauses_until_space_returns_past_the_resume_margin() -> None:
    disk = _Disk({_TEMP: GIB, _HOME: GIB, _WORKSPACE: 400 * GIB})
    clock = FakeClock()
    freed = iter([int(2.5 * GIB), 4 * GIB])

    def free_some_space() -> None:
        disk.free[_TEMP] = disk.free[_HOME] = next(freed)

    waited = _guard(disk, clock, on_wait=free_some_space).wait_until_clear()

    assert waited == pytest.approx(30.0)
    assert disk.free[_TEMP] == 4 * GIB


def test_out_of_space_is_recognised_through_wrapped_errors() -> None:
    wrapped = RuntimeError("Commit review rollback refused")
    wrapped.__cause__ = OSError(errno.ENOSPC, "disk full")

    assert is_out_of_space(wrapped)
    assert is_out_of_space(RuntimeError("Failed to seal proposal tree: [Errno 28] No space"))
    assert not is_out_of_space(RuntimeError("merge conflict"))


def test_a_step_that_runs_out_of_space_is_retried_once_space_returns() -> None:
    disk = _Disk({_TEMP: 50 * GIB, _HOME: 50 * GIB, _WORKSPACE: 400 * GIB})
    clock = FakeClock()
    attempts: list[int] = []

    def step() -> str:
        attempts.append(len(attempts))
        if len(attempts) == 1:
            disk.free[_TEMP] = 0
            raise OSError(errno.ENOSPC, "No space left on device")
        return "advanced"

    def restore() -> None:
        disk.free[_TEMP] = 50 * GIB

    guard = _guard(disk, clock, on_wait=restore)
    result = run_step_with_storage_guard(step, guard=guard, is_failure=lambda _r: False)

    assert result == "advanced"
    assert len(attempts) == 2


def test_a_failure_produced_while_the_disk_is_full_is_retried_not_accepted() -> None:
    disk = _Disk({_TEMP: 50 * GIB, _HOME: 50 * GIB, _WORKSPACE: 400 * GIB})
    clock = FakeClock()
    outcomes = iter(["failed_terminal", "advanced"])
    forgiven: list[int] = []

    def step() -> str:
        outcome = next(outcomes)
        if outcome == "failed_terminal":
            disk.free[_TEMP] = 0
        return outcome

    def restore() -> None:
        disk.free[_TEMP] = 50 * GIB

    result = run_step_with_storage_guard(
        step,
        guard=_guard(disk, clock, on_wait=restore),
        is_failure=lambda outcome: outcome == "failed_terminal",
        on_low_space_outcome=lambda: forgiven.append(1),
    )

    assert result == "advanced"
    assert forgiven == [1]


def test_a_success_while_space_is_low_is_kept_but_its_cooldowns_are_lifted() -> None:
    disk = _Disk({_TEMP: 50 * GIB, _HOME: 50 * GIB, _WORKSPACE: 400 * GIB})
    forgiven: list[int] = []

    def step() -> str:
        disk.free[_TEMP] = GIB
        return "advanced"

    result = run_step_with_storage_guard(
        step,
        guard=_guard(disk, FakeClock()),
        is_failure=lambda _outcome: False,
        on_low_space_outcome=lambda: forgiven.append(1),
    )

    assert result == "advanced"
    assert forgiven == [1]


def test_errors_unrelated_to_disk_space_propagate() -> None:
    disk = _Disk({_TEMP: 50 * GIB, _HOME: 50 * GIB, _WORKSPACE: 400 * GIB})

    def step() -> str:
        msg = "boom"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="boom"):
        run_step_with_storage_guard(
            step, guard=_guard(disk, FakeClock()), is_failure=lambda _r: False
        )


def test_an_out_of_space_error_no_watched_volume_explains_stops_after_bounded_retries() -> None:
    disk = _Disk({_TEMP: 50 * GIB, _HOME: 50 * GIB, _WORKSPACE: 400 * GIB})
    attempts: list[int] = []

    def step() -> str:
        attempts.append(1)
        raise OSError(errno.ENOSPC, "No space left on device")

    with pytest.raises(OSError, match="No space left"):
        run_step_with_storage_guard(
            step, guard=_guard(disk, FakeClock()), is_failure=lambda _r: False
        )
    assert len(attempts) == MAX_UNCONFIRMED_OUT_OF_SPACE_RETRIES
