from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

import ralph.cli.main as ralph_cli_main
import ralph.interrupt as ralph_interrupt
import ralph.interrupt.dispatcher as dispatcher_mod
from ralph.interrupt.controller import INTERRUPT_EXIT_CODE
from ralph.interrupt.dispatcher import dispatcher_from_process_manager
from ralph.process.manager import ProcessManagerPolicy, ProcessRecord, ProcessStatus

if TYPE_CHECKING:
    from collections.abc import Callable


_PID = 201
_PGID = 8888
_FAKE_DEFAULT_GRACE = 2.5
_INVOKE_GRACE = 0.1
_POLL_INTERVAL = 0.01
_QUICK_BUDGET = 0.05


@dataclass
class _FakeClock:
    _t: float = 0.0

    def now(self) -> float:
        return self._t

    def advance(self, s: float) -> None:
        self._t += s


@dataclass
class _RecordingManager:
    policy: ProcessManagerPolicy = field(
        default_factory=lambda: ProcessManagerPolicy(default_grace_period_s=_FAKE_DEFAULT_GRACE)
    )
    shutdown_all_for_label_calls: list[tuple[str, float]] = field(default_factory=list)
    shutdown_all_calls: list[float] = field(default_factory=list)
    kill_process_group_calls: list[tuple[int, int]] = field(default_factory=list)
    _active_records: list[ProcessRecord] = field(default_factory=list)

    def add_active(self, pid: int, pgid: int, label: str = "invoke:fake") -> ProcessRecord:
        record = ProcessRecord(
            pid=pid,
            pgid=pgid,
            command=("fake",),
            cwd=None,
            started_at=datetime.now(tz=UTC),
            status=ProcessStatus.RUNNING,
            label=label,
        )
        self._active_records.append(record)
        return record

    def drain(self) -> None:
        self._active_records.clear()

    def shutdown_all(self, *, grace_period_s: float | None = None) -> None:
        self.shutdown_all_calls.append(grace_period_s if grace_period_s is not None else 0.0)

    def shutdown_all_for_label(
        self, label_prefix: str, *, grace_period_s: float | None = None
    ) -> None:
        self.shutdown_all_for_label_calls.append(
            (label_prefix, grace_period_s if grace_period_s is not None else 0.0)
        )

    def list_active(self) -> list[ProcessRecord]:
        return list(self._active_records)

    def kill_process_group(self, pgid: int, sig: int) -> None:
        self.kill_process_group_calls.append((pgid, sig))

    def register_listener(self, callback: object) -> Callable[[], None]:
        del callback
        return lambda: None


def _import_helper() -> object:
    return ralph_interrupt.handle_keyboard_interrupt_at_cli


def test_handle_keyboard_interrupt_at_cli_returns_exit_code_130() -> None:
    helper = _import_helper()
    manager = _RecordingManager()
    result = helper(process_manager=manager)
    assert result == INTERRUPT_EXIT_CODE


def test_handle_keyboard_interrupt_at_cli_blocks_until_list_active_empty() -> None:
    helper = _import_helper()
    manager = _RecordingManager()
    manager.add_active(pid=_PID, pgid=_PGID)

    def _drain_sleep(s: float) -> None:
        if manager._active_records:
            manager.drain()

    real_factory = dispatcher_from_process_manager

    def _spy(**kwargs: object) -> object:
        if "sleep" not in kwargs:
            kwargs["sleep"] = _drain_sleep
        return real_factory(**kwargs)

    dispatcher_mod.dispatcher_from_process_manager = _spy
    try:
        result = helper(process_manager=manager, poll_interval_s=_POLL_INTERVAL)
    finally:
        dispatcher_mod.dispatcher_from_process_manager = real_factory
    assert result == INTERRUPT_EXIT_CODE
    assert manager.shutdown_all_for_label_calls, "shutdown_all_for_label must be called"
    label, grace = manager.shutdown_all_for_label_calls[0]
    assert label == "invoke:"
    assert grace == _FAKE_DEFAULT_GRACE


def test_handle_keyboard_interrupt_at_cli_returns_after_grace_with_stuck_record() -> None:
    helper = _import_helper()
    clock = _FakeClock()
    manager = _RecordingManager()
    manager.add_active(pid=_PID, pgid=_PGID)
    exit_calls: list[tuple[int, ...]] = []

    def _fake_sleep(s: float) -> None:
        clock.advance(s)

    real_factory = dispatcher_from_process_manager

    def _spy(**kwargs: object) -> object:
        if "process_manager" not in kwargs:
            kwargs["process_manager"] = manager
        if "hard_exit" not in kwargs:
            kwargs["hard_exit"] = lambda code: exit_calls.append((code,))
        if "clock" not in kwargs:
            kwargs["clock"] = clock.now
        if "sleep" not in kwargs:
            kwargs["sleep"] = _fake_sleep
        return real_factory(**kwargs)

    dispatcher_mod.dispatcher_from_process_manager = _spy
    try:
        result = helper(
            process_manager=manager,
            poll_interval_s=_POLL_INTERVAL,
            hard_kill_budget_s=_QUICK_BUDGET,
        )
    finally:
        dispatcher_mod.dispatcher_from_process_manager = real_factory
    assert result == INTERRUPT_EXIT_CODE


def test_handle_keyboard_interrupt_at_cli_records_interrupt_exactly_once() -> None:
    helper = _import_helper()
    manager = _RecordingManager()
    record_calls: list[None] = []
    result = helper(
        process_manager=manager,
        record_interrupt=lambda: record_calls.append(None),
    )
    assert result == INTERRUPT_EXIT_CODE
    assert record_calls == [None]


def test_handle_keyboard_interrupt_at_cli_uses_injected_process_manager_not_singleton() -> None:
    helper = _import_helper()
    manager = _RecordingManager()
    seen: list[object] = []
    real_factory = dispatcher_from_process_manager

    def _spy(**kwargs: object) -> object:
        seen.append(kwargs.get("process_manager"))
        return real_factory(**kwargs)

    dispatcher_mod.dispatcher_from_process_manager = _spy
    try:
        helper(process_manager=manager)
    finally:
        dispatcher_mod.dispatcher_from_process_manager = real_factory
    assert seen, "dispatcher_from_process_manager was not invoked"
    assert seen[0] is manager


def test_handle_keyboard_interrupt_at_cli_honors_exit_code_override() -> None:
    helper = _import_helper()
    manager = _RecordingManager()
    result = helper(process_manager=manager, exit_code=42)
    assert result == 42


def test_handle_keyboard_interrupt_at_cli_propagates_dispatcher_failures() -> None:
    helper = _import_helper()
    real_factory = dispatcher_from_process_manager

    def _boom(**kwargs: object) -> object:
        raise RuntimeError("boom")

    dispatcher_mod.dispatcher_from_process_manager = _boom
    try:
        with pytest.raises(RuntimeError, match="boom"):
            helper(process_manager=_RecordingManager())
    finally:
        dispatcher_mod.dispatcher_from_process_manager = real_factory


def test_handle_keyboard_interrupt_at_cli_uses_kill_label_invoke_by_default() -> None:
    helper = _import_helper()
    manager = _RecordingManager()
    helper(process_manager=manager)
    assert manager.shutdown_all_for_label_calls, "shutdown_all_for_label was not called"
    assert manager.shutdown_all_for_label_calls[0][0] == "invoke:"


def test_handle_keyboard_interrupt_at_cli_blocks_with_block_true_in_real_helper() -> None:
    block_kwargs: list[bool] = []
    original_begin = dispatcher_mod.InterruptDispatcher.__dict__["begin_interrupt"]

    def _spy_begin(self: object, *args: object, **kwargs: object) -> object:
        block_kwargs.append(bool(kwargs.get("block", False)))
        return original_begin(self, *args, **kwargs)

    dispatcher_mod.InterruptDispatcher.begin_interrupt = _spy_begin
    try:
        original_run_pipeline = ralph_cli_main.run_pipeline

        def _raise_kbi(*_args: object, **_kwargs: object) -> int:
            raise KeyboardInterrupt()

        ralph_cli_main.run_pipeline = _raise_kbi
        try:
            return_code = ralph_cli_main._run_pipeline(
                config=None,
                opts=ralph_cli_main._RunPipelineOpts(
                    cli_overrides={},
                    dry_run=False,
                    resume=False,
                    no_resume=False,
                ),
                display_context=type(
                    "_StubDisplay",
                    (),
                    {
                        "console": type(
                            "_StubConsole",
                            (),
                            {
                                "print": lambda self, *a, **kw: None,
                            },
                        )(),
                    },
                )(),
            )
        finally:
            ralph_cli_main.run_pipeline = original_run_pipeline
    finally:
        dispatcher_mod.InterruptDispatcher.begin_interrupt = original_begin
    assert block_kwargs, "begin_interrupt was not called"
    assert block_kwargs == [True]
    assert return_code == 130, (
        f"_run_pipeline must return 130 from the KeyboardInterrupt catch; got {return_code}"
    )
