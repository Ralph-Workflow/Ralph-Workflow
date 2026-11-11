from __future__ import annotations

from ralph.mcp.explore._burst_scheduler import BurstDebounceScheduler


class _FakeClock:
    def __init__(self, initial: float = 0.0) -> None:
        self._t = initial

    def __call__(self) -> float:
        return self._t

    def advance(self, delta: float) -> None:
        self._t += delta


def _make_scheduler() -> tuple[BurstDebounceScheduler, _FakeClock, list[int]]:
    clock = _FakeClock()
    fire_log: list[int] = []

    def on_fire() -> None:
        fire_log.append(1)

    scheduler = BurstDebounceScheduler(clock=clock, on_fire=on_fire, debounce_window=1.0)
    return scheduler, clock, fire_log


def test_200_marks_coalesce_into_one_fire() -> None:
    scheduler, clock, fire_log = _make_scheduler()
    invoked: list[int] = []

    def closure() -> None:
        invoked.append(1)

    for _ in range(200):
        scheduler.mark(closure)
    assert len(fire_log) == 0
    clock.advance(1.0)
    assert scheduler.fire_if_due() is True
    assert len(fire_log) == 1
    assert len(invoked) == 0


def test_on_workflow_complete_releases_pending() -> None:
    scheduler, clock, fire_log = _make_scheduler()
    scheduler.mark(lambda: None)
    scheduler.on_workflow_complete()
    clock.advance(1.0)
    assert scheduler.fire_if_due() is False
    assert len(fire_log) == 0


def test_on_workflow_cancel_releases_pending() -> None:
    scheduler, clock, fire_log = _make_scheduler()
    scheduler.mark(lambda: None)
    scheduler.on_workflow_cancel()
    clock.advance(1.0)
    assert scheduler.fire_if_due() is False
    assert len(fire_log) == 0


def test_on_workflow_fail_releases_pending() -> None:
    scheduler, clock, fire_log = _make_scheduler()
    scheduler.mark(lambda: None)
    scheduler.on_workflow_fail()
    clock.advance(1.0)
    assert scheduler.fire_if_due() is False
    assert len(fire_log) == 0


def test_on_workflow_restart_releases_then_rearms() -> None:
    scheduler, clock, fire_log = _make_scheduler()
    scheduler.mark(lambda: None)
    scheduler.on_workflow_restart()
    clock.advance(1.0)
    assert scheduler.fire_if_due() is False
    assert len(fire_log) == 0
    scheduler.mark(lambda: None)
    clock.advance(1.0)
    assert scheduler.fire_if_due() is True
    assert len(fire_log) == 1


def test_hooks_idempotent_and_leave_counter_unchanged() -> None:
    for hook_name in (
        "on_workflow_complete",
        "on_workflow_cancel",
        "on_workflow_fail",
        "on_workflow_restart",
    ):
        scheduler, _clock, fire_log = _make_scheduler()
        scheduler.mark(lambda: None)
        hook = getattr(scheduler, hook_name)
        hook()
        hook()
        assert len(fire_log) == 0
