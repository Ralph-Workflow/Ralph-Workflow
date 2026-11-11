from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.interrupt.controller import (
    InterruptController,
    controller_from_process_manager,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class _CallRecorder:
    def __init__(
        self,
        for_label_calls: list[tuple[str, float]],
        all_calls: list[float],
    ) -> None:
        self._for_label_calls = for_label_calls
        self._all_calls = all_calls

    def record_for_label(self, label: str, grace_period_s: float) -> None:
        self._for_label_calls.append((label, grace_period_s))

    def record_all(self, grace_period_s: float) -> None:
        self._all_calls.append(grace_period_s)


class _EventRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def record(self, kind: str, payload: object) -> None:
        self.events.append((kind, payload))

    def record_interrupt(self) -> None:
        self.events.append(("record", None))

    def stop_connectivity(self) -> None:
        self.events.append(("stop", None))

    def record_all(self, grace_period_s: float) -> None:
        self.events.append(("shutdown_all", grace_period_s))

    def record_for_label(self, label: str, grace_period_s: float) -> None:
        self.events.append(("shutdown_for_label", (label, grace_period_s)))


def _no_op_shutdown(grace_period_s: float) -> None:
    pass


def _no_op_record() -> None:
    pass


def _build_controller(
    *,
    shutdown_all: Callable[[float], None] | None = None,
    shutdown_all_for_label: Callable[[str, float], None] | None = None,
    record_interrupt: Callable[[], None] | None = None,
    stop_connectivity: Callable[[], None] | None = None,
    kill_process_group: Callable[[int, int], None] | None = None,
    hard_exit: Callable[[int], None] | None = None,
) -> InterruptController:
    return InterruptController(
        shutdown_all=shutdown_all or _no_op_shutdown,
        shutdown_all_for_label=shutdown_all_for_label,
        record_interrupt=record_interrupt or _no_op_record,
        stop_connectivity=stop_connectivity,
        kill_process_group=kill_process_group,
        hard_exit=hard_exit,
    )


def test_begin_interrupt_with_kill_label_calls_shutdown_all_for_label() -> None:
    for_label_calls: list[tuple[str, float]] = []
    all_calls: list[float] = []
    recorder = _CallRecorder(for_label_calls, all_calls)
    controller = _build_controller(
        shutdown_all=recorder.record_all,
        shutdown_all_for_label=recorder.record_for_label,
    )
    controller.begin_interrupt(grace_period_s=0.5, kill_label="invoke:claude")

    assert for_label_calls == [("invoke:claude", 0.5)]
    assert all_calls == [], (
        "shutdown_all must NOT be called when shutdown_all_for_label is set "
        "and kill_label is non-empty"
    )


def test_begin_interrupt_without_kill_label_falls_back_to_shutdown_all() -> None:
    for_label_calls: list[tuple[str, float]] = []
    all_calls: list[float] = []
    recorder = _CallRecorder(for_label_calls, all_calls)
    controller = _build_controller(
        shutdown_all=recorder.record_all,
        shutdown_all_for_label=recorder.record_for_label,
    )
    controller.begin_interrupt(grace_period_s=0.5)

    assert for_label_calls == []
    assert all_calls == [0.5]


def test_begin_interrupt_with_kill_label_falls_back_when_label_closure_unset() -> None:
    all_calls: list[float] = []
    recorder = _CallRecorder([], all_calls)
    controller = _build_controller(
        shutdown_all=recorder.record_all,
        shutdown_all_for_label=None,
    )
    controller.begin_interrupt(grace_period_s=0.5, kill_label="invoke:claude")

    assert all_calls == [0.5]


def test_begin_interrupt_records_interrupt_and_stops_connectivity() -> None:
    events = _EventRecorder()
    controller = _build_controller(
        shutdown_all=events.record_all,
        shutdown_all_for_label=events.record_for_label,
        record_interrupt=events.record_interrupt,
        stop_connectivity=events.stop_connectivity,
    )
    controller.begin_interrupt(grace_period_s=0.5, kill_label="invoke:claude")

    assert events.events[0] == ("record", None)
    assert ("stop", None) in events.events
    assert events.events[-1] == ("shutdown_for_label", ("invoke:claude", 0.5))


def test_controller_from_process_manager_wires_shutdown_all_for_label() -> None:

    class FakeManager:
        def __init__(self) -> None:
            self.shutdown_all_calls: list[float] = []
            self.shutdown_all_for_label_calls: list[tuple[str, float]] = []
            self.policy = type("P", (), {"default_grace_period_s": 1.0})()

        def shutdown_all(self, grace_period_s: float) -> None:
            self.shutdown_all_calls.append(grace_period_s)

        def shutdown_all_for_label(self, label_prefix: str, grace_period_s: float) -> None:
            self.shutdown_all_for_label_calls.append((label_prefix, grace_period_s))

    manager = FakeManager()
    controller = controller_from_process_manager(process_manager=manager)

    assert controller.shutdown_all_for_label is not None
    controller.begin_interrupt(grace_period_s=0.5, kill_label="invoke:foo")
    assert manager.shutdown_all_for_label_calls == [("invoke:foo", 0.5)]
    assert manager.shutdown_all_calls == []


def test_controller_from_process_manager_empty_label_falls_back() -> None:

    class FakeManager:
        def __init__(self) -> None:
            self.shutdown_all_calls: list[float] = []
            self.shutdown_all_for_label_calls: list[tuple[str, float]] = []
            self.policy = type("P", (), {"default_grace_period_s": 1.0})()

        def shutdown_all(self, grace_period_s: float) -> None:
            self.shutdown_all_calls.append(grace_period_s)

        def shutdown_all_for_label(self, label_prefix: str, grace_period_s: float) -> None:
            self.shutdown_all_for_label_calls.append((label_prefix, grace_period_s))

    manager = FakeManager()
    controller = controller_from_process_manager(process_manager=manager)

    controller.begin_interrupt(grace_period_s=0.5)
    assert manager.shutdown_all_calls == [0.5]
    assert manager.shutdown_all_for_label_calls == []


def test_begin_interrupt_with_empty_kill_label_kwarg_uses_shutdown_all() -> None:
    for_label_calls: list[tuple[str, float]] = []
    all_calls: list[float] = []
    recorder = _CallRecorder(for_label_calls, all_calls)
    controller = _build_controller(
        shutdown_all=recorder.record_all,
        shutdown_all_for_label=recorder.record_for_label,
    )
    controller.begin_interrupt(grace_period_s=0.5, kill_label="")
    assert for_label_calls == []
    assert all_calls == [0.5]


@pytest.mark.parametrize("kill_label", ["invoke:claude", "phase:dev:mcp-server", "agent-x"])
def test_begin_interrupt_passes_kill_label_to_closure(kill_label: str) -> None:
    for_label_calls: list[tuple[str, float]] = []
    all_calls: list[float] = []
    recorder = _CallRecorder(for_label_calls, all_calls)
    controller = _build_controller(
        shutdown_all=recorder.record_all,
        shutdown_all_for_label=recorder.record_for_label,
    )
    controller.begin_interrupt(grace_period_s=0.5, kill_label=kill_label)
    assert for_label_calls == [(kill_label, 0.5)]
