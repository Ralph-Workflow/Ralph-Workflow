"""Unit tests for ``BurstDebounceScheduler`` (S-3a).

Coalescing and lifecycle-hook semantics with a ``FakeClock``. The
scheduler is passive: ``mark`` records closures, the owner advances the
clock, and the fire happens when ``fire_if_due`` observes the debounce
window has elapsed.
"""

from __future__ import annotations

from ralph.mcp.explore._burst_scheduler import BurstDebounceScheduler


class _FakeClock:
    """Controllable monotonic clock for deterministic debounce tests."""

    def __init__(self, initial: float = 0.0) -> None:
        self._t = initial

    def __call__(self) -> float:
        return self._t

    def advance(self, delta: float) -> None:
        self._t += delta


def _make_scheduler() -> tuple[BurstDebounceScheduler, _FakeClock, list[int]]:
    """Build a scheduler wired to a fire counter.

    Returns ``(scheduler, clock, fire_log)`` where ``fire_log`` records
    one entry per coalesced fire.
    """
    clock = _FakeClock()
    fire_log: list[int] = []

    def on_fire() -> None:
        fire_log.append(1)

    scheduler = BurstDebounceScheduler(
        clock=clock, on_fire=on_fire, debounce_window=1.0
    )
    return scheduler, clock, fire_log


def test_200_marks_coalesce_into_one_fire() -> None:
    """A burst of 200 marks in one window fires exactly once.

    The closures are evidence that work is pending; the coalesced fire
    invokes ``on_fire`` once (the single reindex trigger), not once per
    closure. The closure bodies are never invoked by the scheduler.
    """
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
    # Closures are evidence only; the scheduler never invokes them.
    assert len(invoked) == 0


def test_on_workflow_complete_releases_pending() -> None:
    """``on_workflow_complete`` drops the pending fire."""
    scheduler, clock, fire_log = _make_scheduler()
    scheduler.mark(lambda: None)
    scheduler.on_workflow_complete()
    clock.advance(1.0)
    assert scheduler.fire_if_due() is False
    assert len(fire_log) == 0


def test_on_workflow_cancel_releases_pending() -> None:
    """``on_workflow_cancel`` drops the pending fire."""
    scheduler, clock, fire_log = _make_scheduler()
    scheduler.mark(lambda: None)
    scheduler.on_workflow_cancel()
    clock.advance(1.0)
    assert scheduler.fire_if_due() is False
    assert len(fire_log) == 0


def test_on_workflow_fail_releases_pending() -> None:
    """``on_workflow_fail`` drops the pending fire."""
    scheduler, clock, fire_log = _make_scheduler()
    scheduler.mark(lambda: None)
    scheduler.on_workflow_fail()
    clock.advance(1.0)
    assert scheduler.fire_if_due() is False
    assert len(fire_log) == 0


def test_on_workflow_restart_releases_then_rearms() -> None:
    """``on_workflow_restart`` drops the pending fire and re-arms."""
    scheduler, clock, fire_log = _make_scheduler()
    scheduler.mark(lambda: None)
    scheduler.on_workflow_restart()
    clock.advance(1.0)
    assert scheduler.fire_if_due() is False
    assert len(fire_log) == 0
    # Re-arm: a fresh mark after restart fires normally.
    scheduler.mark(lambda: None)
    clock.advance(1.0)
    assert scheduler.fire_if_due() is True
    assert len(fire_log) == 1


def test_hooks_idempotent_and_leave_counter_unchanged() -> None:
    """Each hook called twice in a row does not raise and leaves the counter."""
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
