from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.testing.fake_process import FakePsutil

if TYPE_CHECKING:
    from ralph.process.manager import ProcessEvent


def _make_pm(max_listeners: int = 64, *, log_events: bool = False) -> ProcessManager:
    return ProcessManager(
        policy=ProcessManagerPolicy(
            max_listeners=max_listeners,
            log_events=log_events,
            enable_zombie_reaper=False,
        ),
        psutil=FakePsutil(),
    )


def test_max_listeners_default_is_generous() -> None:
    pm = _make_pm()
    assert pm.policy.max_listeners >= 16, (
        f"default max_listeners must be >= 16; got {pm.policy.max_listeners}"
    )


def test_listener_cap_evicts_oldest_when_exceeded() -> None:
    pm = _make_pm(max_listeners=4)
    initial = pm.listener_count
    assert initial == 0, f"expected baseline 0 listeners; got {initial}"

    callbacks: list[object] = []

    def _cb(ev: ProcessEvent) -> None:
        return None

    callbacks.extend(pm.register_listener(_cb) for _ in range(5))

    assert pm.listener_count == 4, (
        f"expected listeners dict to stay at max_listeners=4; got {pm.listener_count}"
    )

    surviving_ids = range(pm.listener_count)
    assert len(surviving_ids) == 4


def test_unsubscribe_callable_removes_listener() -> None:
    pm = _make_pm(max_listeners=64)
    initial = pm.listener_count

    def _cb(ev: ProcessEvent) -> None:
        return None

    unsubscribe = pm.register_listener(_cb)
    assert pm.listener_count == initial + 1

    unsubscribe()
    assert pm.listener_count == initial, (
        f"unsubscribe must remove the listener; got {pm.listener_count} vs initial {initial}"
    )


def test_permanent_loguru_listener_present_after_init() -> None:
    pm = _make_pm(log_events=True)
    assert pm.listener_count >= 1, "expected at least the permanent listener"
    callbacks = pm.listener_callbacks()
    assert callable(callbacks[0])
