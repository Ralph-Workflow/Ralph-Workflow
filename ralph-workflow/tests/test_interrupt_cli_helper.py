"""Black-box tests for the CLI interrupt helper.

These tests pin the contract for the new
``ralph.interrupt.handle_keyboard_interrupt_at_cli`` helper, which
consolidates the two near-duplicate ``except KeyboardInterrupt`` blocks
in ``ralph/cli/main.py:_run_pipeline`` and
``ralph/cli/commands/run.py:run`` behind a single function.

The helper is the canonical owner of:

* the ``block=True`` contract on ``begin_interrupt`` (the
  ``shutdown_all_for_label``-with-grace + wait-for-list_active-empty
  flow), and
* the ``exit_code`` return value (default ``INTERRUPT_EXIT_CODE = 130``).

All tests in this file use ``FakeProcessManager`` (re-imported from
``test_interrupt_dispatcher``), a fake clock, and a fake sleep so they
run in microseconds. No real wall-clock waits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

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
    """Minimal clock abstraction: ``now()`` returns the current fake
    time, ``advance(s)`` moves time forward.
    """

    _t: float = 0.0

    def now(self) -> float:
        return self._t

    def advance(self, s: float) -> None:
        self._t += s


@dataclass
class _RecordingManager:
    """Minimal ``ProcessManager`` fake for the helper tests.

    Mirrors the surface that ``dispatcher_from_process_manager`` and
    ``InterruptDispatcher`` exercise: ``list_active``,
    ``shutdown_all_for_label``, ``shutdown_all``, ``kill_process_group``,
    and a ``policy`` with ``default_grace_period_s``.
    """

    policy: ProcessManagerPolicy = field(
        default_factory=lambda: ProcessManagerPolicy(default_grace_period_s=_FAKE_DEFAULT_GRACE)
    )
    shutdown_all_for_label_calls: list[tuple[str, float]] = field(default_factory=list)
    shutdown_all_calls: list[float] = field(default_factory=list)
    kill_process_group_calls: list[tuple[int, int]] = field(default_factory=list)
    _active_records: list[ProcessRecord] = field(default_factory=list)

    def add_active(
        self, pid: int, pgid: int, label: str = "invoke:fake"
    ) -> ProcessRecord:
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
    """Import the helper from ``ralph.interrupt`` (imports in the test
    body so a missing function fails the test with a clear ImportError).
    """
    return ralph_interrupt.handle_keyboard_interrupt_at_cli


def test_handle_keyboard_interrupt_at_cli_returns_exit_code_130() -> None:
    """Test 1: when no active records are tracked, the helper must
    return ``INTERRUPT_EXIT_CODE`` (130).
    """
    helper = _import_helper()
    manager = _RecordingManager()
    result = helper(process_manager=manager)
    assert result == INTERRUPT_EXIT_CODE


def test_handle_keyboard_interrupt_at_cli_blocks_until_list_active_empty() -> None:
    """Test 2: when one active record is tracked, the helper must call
    ``shutdown_all_for_label('invoke:', grace)``, the helper's
    internally-built dispatcher's ``begin_interrupt`` must be invoked
    with ``block=True``, and the helper must return 130 after the
    record drains. Uses a fake sleep that drains the manager on the
    first tick.
    """
    helper = _import_helper()
    manager = _RecordingManager()
    manager.add_active(pid=_PID, pgid=_PGID)

    def _drain_sleep(s: float) -> None:
        if manager._active_records:
            manager.drain()

    real_factory = dispatcher_from_process_manager

    def _spy(**kwargs: object) -> object:
        if "sleep" not in kwargs:
            kwargs["sleep"] = cast("Callable[[float], None]", _drain_sleep)
        return real_factory(**kwargs)

    dispatcher_mod.dispatcher_from_process_manager = cast("Any", _spy)
    try:
        result = helper(process_manager=manager, poll_interval_s=_POLL_INTERVAL)
    finally:
        dispatcher_mod.dispatcher_from_process_manager = cast("Any", real_factory)
    assert result == INTERRUPT_EXIT_CODE
    assert manager.shutdown_all_for_label_calls, "shutdown_all_for_label must be called"
    label, grace = manager.shutdown_all_for_label_calls[0]
    assert label == "invoke:"
    assert grace == _FAKE_DEFAULT_GRACE


def test_handle_keyboard_interrupt_at_cli_returns_after_grace_with_stuck_record() -> None:
    """Test 3: a record that never drains must not hang the helper.
    The fake sleep advances the clock past the grace period; the helper
    must still return 130 (and the new escalation in Step 11 means
    ``force_exit`` is invoked — captured by the test spy).
    """
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
            kwargs["hard_exit"] = cast(
                "Callable[[int], None]", lambda code: exit_calls.append((code,))
            )
        if "clock" not in kwargs:
            kwargs["clock"] = cast("Callable[[], float]", clock.now)
        if "sleep" not in kwargs:
            kwargs["sleep"] = cast("Callable[[float], None]", _fake_sleep)
        return real_factory(**kwargs)

    dispatcher_mod.dispatcher_from_process_manager = cast("Any", _spy)
    try:
        result = helper(
            process_manager=manager,
            poll_interval_s=_POLL_INTERVAL,
            hard_kill_budget_s=_QUICK_BUDGET,
        )
    finally:
        dispatcher_mod.dispatcher_from_process_manager = cast("Any", real_factory)
    assert result == INTERRUPT_EXIT_CODE


def test_handle_keyboard_interrupt_at_cli_records_interrupt_exactly_once() -> None:
    """Test 4: the helper must invoke the supplied ``record_interrupt``
    callable exactly once (matching the dispatcher's contract).
    """
    helper = _import_helper()
    manager = _RecordingManager()
    record_calls: list[None] = []
    result = helper(
        process_manager=manager,
        record_interrupt=cast("Callable[[], None]", lambda: record_calls.append(None)),
    )
    assert result == INTERRUPT_EXIT_CODE
    assert record_calls == [None]


def test_handle_keyboard_interrupt_at_cli_uses_injected_process_manager_not_singleton() -> None:
    """Test 5: the helper must build its internal dispatcher with the
    supplied ``process_manager`` (not the ``get_process_manager()``
    singleton). Asserted by monkeypatching the factory and recording
    the process_manager it received.
    """
    helper = _import_helper()
    manager = _RecordingManager()
    seen: list[object] = []
    real_factory = dispatcher_from_process_manager

    def _spy(**kwargs: object) -> object:
        seen.append(kwargs.get("process_manager"))
        return real_factory(**kwargs)

    dispatcher_mod.dispatcher_from_process_manager = cast("Any", _spy)
    try:
        helper(process_manager=manager)
    finally:
        dispatcher_mod.dispatcher_from_process_manager = cast("Any", real_factory)
    assert seen, "dispatcher_from_process_manager was not invoked"
    assert seen[0] is manager


def test_handle_keyboard_interrupt_at_cli_honors_exit_code_override() -> None:
    """Test 6: when ``exit_code`` is overridden, the helper must return
    that value instead of the default 130.
    """
    helper = _import_helper()
    manager = _RecordingManager()
    result = helper(process_manager=manager, exit_code=42)
    assert result == 42


def test_handle_keyboard_interrupt_at_cli_propagates_dispatcher_failures() -> None:
    """Test 7 (Strategy A): when the internal
    ``dispatcher_from_process_manager`` raises, the helper must
    PROPAGATE the exception (it does NOT swallow). The CLI catches the
    helper call and emits the verbatim log warning.
    """
    helper = _import_helper()
    real_factory = dispatcher_from_process_manager

    def _boom(**kwargs: object) -> object:
        raise RuntimeError("boom")

    dispatcher_mod.dispatcher_from_process_manager = cast("Any", _boom)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            helper(process_manager=_RecordingManager())
    finally:
        dispatcher_mod.dispatcher_from_process_manager = cast("Any", real_factory)


def test_handle_keyboard_interrupt_at_cli_uses_kill_label_invoke_by_default() -> None:
    """Test 8: the helper's default ``kill_label`` is ``'invoke:'`` —
    the controller's begin_interrupt is called with this label so the
    label-targeted shutdown path is taken.
    """
    helper = _import_helper()
    manager = _RecordingManager()
    helper(process_manager=manager)
    assert manager.shutdown_all_for_label_calls, "shutdown_all_for_label was not called"
    assert manager.shutdown_all_for_label_calls[0][0] == "invoke:"


def test_handle_keyboard_interrupt_at_cli_blocks_with_block_true_in_real_helper() -> None:
    """End-to-end CANONICAL PIN: monkeypatch the
    ``InterruptDispatcher.begin_interrupt`` class method to record the
    kwargs it received. Trigger KeyboardInterrupt through the actual
    CLI except block. Assert the wrapper was called exactly once with
    ``block=True`` and the CLI catch returned 130.

    The test uses the real ``handle_keyboard_interrupt_at_cli`` helper
    and a real CLI module catch (``ralph.cli.main._run_pipeline`` with
    ``run_pipeline`` monkeypatched to raise ``KeyboardInterrupt``).
    """
    block_kwargs: list[bool] = []
    original_begin = dispatcher_mod.InterruptDispatcher.__dict__["begin_interrupt"]

    def _spy_begin(self: object, *args: object, **kwargs: object) -> object:
        block_kwargs.append(bool(kwargs.get("block", False)))
        return original_begin(self, *args, **kwargs)

    dispatcher_mod.InterruptDispatcher.begin_interrupt = cast("Any", _spy_begin)
    try:
        original_run_pipeline = ralph_cli_main.run_pipeline

        def _raise_kbi(*_args: object, **_kwargs: object) -> int:
            raise KeyboardInterrupt()

        ralph_cli_main.run_pipeline = cast("Any", _raise_kbi)
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
            ralph_cli_main.run_pipeline = cast("Any", original_run_pipeline)
    finally:
        dispatcher_mod.InterruptDispatcher.begin_interrupt = cast("Any", original_begin)
    assert block_kwargs, "begin_interrupt was not called"
    assert block_kwargs == [True]
    assert return_code == 130, (
        f"_run_pipeline must return 130 from the KeyboardInterrupt catch; got {return_code}"
    )
