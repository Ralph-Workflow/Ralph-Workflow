"""Black-box tests for the InterruptDispatcher — the single seam for SIGINT handling.

These tests pin the contract for the new dispatcher that the production code
in ``ralph.interrupt.dispatcher`` must satisfy. The dispatcher is the
single seam that wires ``InterruptController`` to ``ProcessManager``, the
connectivity-stop callback, and the hard-exit function. Both the sync
``handle_keyboard_interrupt`` path and the asyncio path build their
dispatchers through the same factory ``dispatcher_from_process_manager``,
so any future change to the wiring happens in one place.

All tests in this file use ``FakeProcessManager`` to avoid touching real
processes, real psutil, or the real ``get_process_manager()`` singleton.
Timing tests use ``poll_interval_s=0.01`` and ``hard_kill_budget_s=0.05``
so the entire file runs well within the 60-second combined test budget.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import pytest

import ralph.cli.commands.run as ralph_cli_run_module
import ralph.interrupt.dispatcher as dispatcher_mod
from ralph.interrupt.asyncio_bridge import SignalBridge, install_signal_handlers
from ralph.interrupt.controller import (
    INTERRUPT_EXIT_CODE,
    InterruptController,
    controller_from_process_manager,
)
from ralph.interrupt.dispatcher import (
    InterruptDispatcher,
    dispatcher_from_process_manager,
    run_shutdown_block,
)
from ralph.pipeline._runner_interrupt import handle_keyboard_interrupt
from ralph.process.manager import ProcessManagerPolicy, ProcessRecord, ProcessStatus

if TYPE_CHECKING:
    from collections.abc import Callable


_PID = 101
_PGID = 9999
_INVOKE_GRACE = 0.1
_QUICK_BUDGET = 0.05
_POLL_INTERVAL = 0.01
_SIGINT = signal.SIGINT
_SIGTERM = signal.SIGTERM
_FAKE_DEFAULT_GRACE = 2.5


@dataclass
class FakeProcessManager:
    """Black-box fake of ``ProcessManager`` for dispatcher tests.

    Only the methods the dispatcher exercises are wired with recording
    callables. Everything else is a no-op so the dispatcher can call
    any reasonable subset of the surface without raising.
    """

    policy: ProcessManagerPolicy = field(
        default_factory=lambda: ProcessManagerPolicy(default_grace_period_s=_FAKE_DEFAULT_GRACE)
    )
    shutdown_all_calls: list[float] = field(default_factory=list)
    shutdown_all_for_label_calls: list[tuple[str, float]] = field(default_factory=list)
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
        resolved = grace_period_s if grace_period_s is not None else 0.0
        self.shutdown_all_calls.append(resolved)
        # AC-11 contract: manager.shutdown_all(0) is the only kill path
        # in the post-fix controller. The fake mirrors the real
        # ProcessManager's escalation behaviour: grace_period_s == 0
        # SIGKILLs every active record so the test can assert the
        # kill through kill_process_group_calls.
        if resolved == 0:
            for record in self._active_records:
                self.kill_process_group_calls.append((record.pgid, signal.SIGKILL))
            self._active_records.clear()

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


def _build_dispatcher(**overrides: object) -> tuple[FakeProcessManager, InterruptDispatcher]:
    """Build an InterruptDispatcher via the factory with sensible defaults."""
    manager = FakeProcessManager()
    exit_calls: list[tuple[int, ...]] = []
    kill_calls: list[tuple[int, int]] = []
    record_calls: list[None] = []

    def _record_exit(code: int) -> None:
        exit_calls.append((code,))

    def _record_kill(pid: int, sig: int) -> None:
        kill_calls.append((pid, sig))

    def _record_interrupt() -> None:
        record_calls.append(None)

    kwargs: dict[str, object] = {
        "process_manager": manager,
        "hard_exit": _record_exit,
        "kill_process_group": _record_kill,
        "record_interrupt": _record_interrupt,
    }
    kwargs.update(overrides)
    dispatcher = dispatcher_from_process_manager(**kwargs)
    return manager, dispatcher


def test_dispatcher_begin_interrupt_calls_label_targeted_shutdown() -> None:
    """When the dispatcher begins an interrupt, it routes through the
    label-targeted shutdown with the dispatcher's default ``kill_label``."""
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")

    _, dispatcher = _build_dispatcher(process_manager=manager)
    dispatcher.begin_interrupt(grace_period_s=_INVOKE_GRACE)

    assert manager.shutdown_all_for_label_calls == [("invoke:", _INVOKE_GRACE)]
    assert manager.shutdown_all_calls == []


def test_dispatcher_force_exit_calls_shutdown_zero_and_hard_exit_with_pgid() -> None:
    """force_exit with bridge_pgids must call shutdown_all(0) and
    hard_exit(INTERRUPT_EXIT_CODE). The kill is recorded by the
    FakePM's ``shutdown_all(grace=0)`` (the AC-11 contract:
    ``manager.shutdown_all(0)`` is the only kill path). The
    dispatcher's force_exit routes through the controller's
    ``force_interrupt``, which now drops the per-pgid loop.
    """
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID)
    kill_calls: list[tuple[int, int]] = []
    exit_calls: list[tuple[int, ...]] = []
    dispatcher = dispatcher_from_process_manager(
        process_manager=manager,
        kill_process_group=cast(
            "Callable[[int, int], None]", lambda pgid, sig: kill_calls.append((pgid, sig))
        ),
        hard_exit=cast("Callable[[int], None]", lambda code: exit_calls.append((code,))),
    )

    dispatcher.force_exit(bridge_pgids=[_PGID])

    assert manager.shutdown_all_calls == [0]
    # FakePM.shutdown_all(0) records kills in kill_process_group_calls
    # to mirror the real ProcessManager's escalation.
    assert manager.kill_process_group_calls == [(_PGID, signal.SIGKILL)]
    assert exit_calls == [(INTERRUPT_EXIT_CODE,)]


def test_dispatcher_factory_wires_process_manager() -> None:
    """The factory wires the dispatcher's controller so calling
    ``dispatcher.hard_exit`` invokes the factory-supplied hard_exit
    (proving the dispatcher owns the hard-exit field, not the
    controller's)."""
    manager = FakeProcessManager()
    exit_calls: list[tuple[int, ...]] = []
    dispatcher = _build_dispatcher(
        process_manager=manager,
        hard_exit=cast("Callable[[int], None]", lambda code: exit_calls.append((code,))),
    )[1]
    assert dispatcher.controller.shutdown_all is not None
    assert dispatcher.controller.shutdown_all_for_label is not None
    dispatcher.hard_exit(INTERRUPT_EXIT_CODE)
    assert exit_calls == [(INTERRUPT_EXIT_CODE,)]


def test_dispatcher_force_exit_is_idempotent_on_repeat_bridge_pids() -> None:
    """PINNED CONTRACT: a second force_exit call is a no-op; hard_exit
    is called exactly once across two invocations. The dispatcher
    enforces this via an internal ``_force_exit_called`` flag set in
    ``__post_init__``; the controller does NOT have this guarantee.
    """
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID)
    _, dispatcher = _build_dispatcher(process_manager=manager)
    dispatcher.force_exit(bridge_pgids=[_PID])
    dispatcher.force_exit(bridge_pgids=[_PID])
    assert manager.shutdown_all_calls == [0]


def test_controller_force_exit_has_no_idempotency_guarantee() -> None:
    """Document the design difference: the raw controller has NO
    idempotency, the dispatcher does. Two force_exit calls on a
    controller execute both — so the dispatcher wrapper is the only
    place that closes the double-invocation gap."""
    exit_calls: list[tuple[int, ...]] = []
    controller = InterruptController(
        shutdown_all=lambda grace_period_s: None,
        record_interrupt=lambda: None,
        hard_exit=cast("Callable[[int], None]", lambda code: exit_calls.append((code,))),
    )
    controller.force_exit(bridge_pgids=[_PID])
    controller.force_exit(bridge_pgids=[_PID])
    assert exit_calls == [(INTERRUPT_EXIT_CODE,), (INTERRUPT_EXIT_CODE,)]


def test_dispatcher_uses_process_manager_default_grace_period_when_none() -> None:
    """When ``grace_period_s`` is None, the dispatcher converts to the
    ProcessManager's policy default BEFORE calling the controller,
    because the controller rejects None."""
    manager = FakeProcessManager()
    manager.policy = ProcessManagerPolicy(default_grace_period_s=2.5)
    manager.shutdown_all_for_label_calls = []
    manager._active_records.clear()

    dispatcher = dispatcher_from_process_manager(
        process_manager=manager,
        hard_exit=cast("Callable[[int], None]", lambda _c: None),
    )
    dispatcher.begin_interrupt(grace_period_s=None)

    assert manager.shutdown_all_for_label_calls == [("invoke:", 2.5)]


def test_dispatcher_force_exit_calls_record_interrupt_exactly_once() -> None:
    """force_exit on the dispatcher calls the controller's
    record_interrupt exactly once (mirrors begin_interrupt's contract).
    A regression that invokes record_interrupt twice would inflate
    the interrupt counter."""
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID)
    record_calls: list[None] = []
    _, dispatcher = _build_dispatcher(
        process_manager=manager,
        record_interrupt=cast("Callable[[], None]", lambda: record_calls.append(None)),
    )
    dispatcher.force_exit(bridge_pgids=[_PID])
    assert record_calls == [None]


def test_interrupt_dispatcher_constructor_rejects_invalid_budget() -> None:
    """The dataclass ``__post_init__`` must raise ``RuntimeError`` for
    a non-positive budget, mirroring the existing import-time invariant
    in ``_runner_interrupt.py``. This proves the budget is enforced at
    construction time, not just at use."""
    manager = FakeProcessManager()
    with pytest.raises(RuntimeError):
        InterruptDispatcher(
            controller=InterruptController(shutdown_all=lambda _g: None),
            process_manager=manager,
            hard_exit=cast("Callable[[int], None]", lambda _c: None),
            poll_interval_s=0.01,
            hard_kill_budget_s=0,
        )


def test_dispatcher_factory_uses_explicit_process_manager_not_singleton() -> None:
    """Construct a dispatcher with an explicit ``process_manager`` and
    assert the dispatcher stores the SAME instance (not the singleton
    returned by ``get_process_manager()``). Pins the explicit-process_manager
    discipline the DI contract relies on."""
    manager = FakeProcessManager()
    dispatcher = dispatcher_from_process_manager(
        process_manager=manager,
        hard_exit=cast("Callable[[int], None]", lambda _c: None),
    )
    assert dispatcher.process_manager is manager


def _patch_psutil(monkeypatch: pytest.MonkeyPatch, cpu_factory: Callable[[int], float]) -> None:
    """Patch ``ralph.interrupt.dispatcher.importlib.import_module`` to
    return a psutil stub whose ``Process(pid).cpu_times()`` returns
    ``(user=cpu_factory(call_n), system=0)``. The ``cpu_factory`` receives
    the call number (starting at 1) and must return the cumulative CPU
    time for that poll.
    """

    class _FakeCpu:
        def __init__(self, user: float) -> None:
            self.user = user
            self.system = 0.0

    class _FakeProc:
        def __init__(self) -> None:
            self._n = 0

        def cpu_times(self) -> _FakeCpu:
            self._n += 1
            return _FakeCpu(cpu_factory(self._n))

    class _FakePsutil:
        @staticmethod
        def get_process(_pid: int) -> _FakeProc:
            return _FakeProc()

    _FakePsutil.Process = staticmethod(_FakePsutil.get_process)

    real_import = dispatcher_mod.importlib.import_module

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "psutil":
            return _FakePsutil
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(dispatcher_mod.importlib, "import_module", _fake_import)


def _patch_psutil_shared(
    monkeypatch: pytest.MonkeyPatch, cpu_factory: Callable[[int], float]
) -> None:
    """Same as ``_patch_psutil`` but a single ``_FakeProc`` instance is
    returned for the same PID across calls so the call counter is
    preserved (matches real psutil behavior where the same ``Process``
    handle persists).
    """

    class _FakeCpu:
        def __init__(self, user: float) -> None:
            self.user = user
            self.system = 0.0

    class _FakeProc:
        def __init__(self) -> None:
            self._n = 0

        def cpu_times(self) -> _FakeCpu:
            self._n += 1
            return _FakeCpu(cpu_factory(self._n))

    instances: dict[int, _FakeProc] = {}

    class _FakePsutil:
        @staticmethod
        def get_process(pid: int) -> _FakeProc:
            if pid not in instances:
                instances[pid] = _FakeProc()
            return instances[pid]

    _FakePsutil.Process = staticmethod(_FakePsutil.get_process)

    real_import = dispatcher_mod.importlib.import_module

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "psutil":
            return _FakePsutil
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(dispatcher_mod.importlib, "import_module", _fake_import)


def _patch_pid_alive(monkeypatch: pytest.MonkeyPatch, *, alive: bool) -> None:
    """Patch ``os.kill`` in the dispatcher module so ``_pid_is_alive``
    returns ``alive`` (regardless of the real PID).
    """

    def _fake_kill(_pid: int, _sig: int) -> None:
        if not alive:
            raise ProcessLookupError(_pid)

    monkeypatch.setattr(dispatcher_mod.os, "kill", _fake_kill)


def test_early_escalation_poll_kills_when_no_cpu_progress_within_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The early-escalation poll must send SIGKILL to the matched
    record's PGID when no CPU-time progress is detected within the
    hard-kill budget. Stubs psutil via ``import_module`` patching.

    NOTE: ``run_early_escalation_poll`` is a public utility kept for
    backward compatibility but is NOT used by the production seam.
    The production seam in ``run_shutdown_block`` uses
    ``begin_interrupt(block=True)`` which routes through the
    dispatcher's liveness-based ``_wait_for_list_active_empty``.
    This test pins the public-method contract so a future regression
    in the method body is caught.
    """
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:claude")
    manager.kill_process_group_calls = []
    _patch_psutil(monkeypatch, lambda _call_n: 1.0)
    _patch_pid_alive(monkeypatch, alive=True)

    dispatcher = InterruptDispatcher(
        controller=InterruptController(
            shutdown_all=lambda _g: None,
            shutdown_all_for_label=lambda _l, _g: None,
        ),
        process_manager=manager,
        hard_exit=cast("Callable[[int], None]", lambda _c: None),
        poll_interval_s=_POLL_INTERVAL,
        hard_kill_budget_s=_QUICK_BUDGET,
    )
    dispatcher.run_early_escalation_poll(max_wait_s=_QUICK_BUDGET)
    assert manager.kill_process_group_calls == [(_PGID, signal.SIGKILL)]


def test_early_escalation_poll_does_not_kill_when_cpu_progresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When CPU time INCREASES across polls, the agent is making
    progress; the poll must NOT send SIGKILL.

    NOTE: ``run_early_escalation_poll`` is a public utility kept for
    backward compatibility but is NOT used by the production seam.
    The production seam in ``run_shutdown_block`` uses
    ``begin_interrupt(block=True)`` which routes through the
    dispatcher's liveness-based ``_wait_for_list_active_empty``.
    This test pins the public-method contract so a future regression
    in the method body is caught.
    """
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:claude")
    manager.kill_process_group_calls = []
    _patch_psutil_shared(monkeypatch, float)
    _patch_pid_alive(monkeypatch, alive=True)

    dispatcher = InterruptDispatcher(
        controller=InterruptController(
            shutdown_all=lambda _g: None,
            shutdown_all_for_label=lambda _l, _g: None,
        ),
        process_manager=manager,
        hard_exit=cast("Callable[[int], None]", lambda _c: None),
        poll_interval_s=_POLL_INTERVAL,
        hard_kill_budget_s=_QUICK_BUDGET,
    )
    dispatcher.run_early_escalation_poll(max_wait_s=_QUICK_BUDGET)
    assert manager.kill_process_group_calls == []


def test_early_escalation_poll_exits_when_process_dies(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the matched process is no longer alive at the OS level
    from the first poll, the function must return without blocking


pytestmark = pytest.mark.subprocess_e2e
    for the full budget. Pins the early-return path on liveness check
    failure (no SIGKILL sent).

    NOTE: ``run_early_escalation_poll`` is a public utility kept for
    backward compatibility but is NOT used by the production seam.
    The production seam in ``run_shutdown_block`` uses
    ``begin_interrupt(block=True)`` which routes through the
    dispatcher's liveness-based ``_wait_for_list_active_empty``.
    This test pins the public-method contract so a future regression
    in the method body is caught.
    """
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:claude")
    manager.kill_process_group_calls = []

    def _kill_returns_dead(_pid: int, _sig: int) -> None:
        raise ProcessLookupError(_pid)

    monkeypatch.setattr(dispatcher_mod.os, "kill", _kill_returns_dead)

    dispatcher = InterruptDispatcher(
        controller=InterruptController(
            shutdown_all=lambda _g: None,
            shutdown_all_for_label=lambda _l, _g: None,
        ),
        process_manager=manager,
        hard_exit=cast("Callable[[int], None]", lambda _c: None),
        poll_interval_s=_POLL_INTERVAL,
        hard_kill_budget_s=_QUICK_BUDGET,
    )
    dispatcher.run_early_escalation_poll(max_wait_s=_QUICK_BUDGET)
    assert manager.kill_process_group_calls == []


def test_dispatcher_begin_interrupt_block_true_blocks_until_list_active_empty() -> None:
    """When ``block=True``, the dispatcher must wait until
    ``process_manager.list_active()`` is empty (or the grace period
    elapses). A regression that drops block=True leaves the agent's
    process group orphaned after a CLI Ctrl+C catch.

    Uses a thread that drains the manager's active list shortly after
    begin_interrupt blocks. A barrier primitive (threading.Event) is
    used to coordinate so the test does not depend on wall-clock
    measurements.
    """
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")

    _, dispatcher = _build_dispatcher(process_manager=manager)
    drained = threading.Event()
    begin_called = threading.Event()

    def _drain_after_begin() -> None:
        begin_called.wait(timeout=0.5)
        manager.drain()
        drained.set()

    drainer = threading.Thread(target=_drain_after_begin, daemon=True)
    drainer.start()
    dispatcher.begin_interrupt(block=True, grace_period_s=_INVOKE_GRACE)
    begin_called.set()
    assert drained.wait(timeout=0.5)
    drainer.join(timeout=0.1)
    assert manager.shutdown_all_for_label_calls == [("invoke:", _INVOKE_GRACE)]


class _FakeClock:
    """Minimal clock abstraction for clock-seam tests.

    A replacement for ``time.monotonic`` that the dispatcher reads through
    ``self.clock()``. Each ``advance(s)`` call moves time forward by
    ``s`` seconds; the next ``now()`` returns the new value.
    """

    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def advance(self, s: float) -> None:
        self._t += s


def _make_fake_sleep(clock: _FakeClock) -> Callable[[float], None]:
    """Build a fake sleep that advances the clock on every call."""

    def _fake_sleep(s: float) -> None:
        clock.advance(s)

    return _fake_sleep


def test_dispatcher_uses_injected_clock_for_block_wait_deadline() -> None:
    """Test 1: dispatcher must read its ``clock`` field (not call
    ``time.monotonic`` directly) when computing the block-wait deadline
    in ``_wait_for_list_active_empty``. A fake clock and fake sleep let
    the test run in microseconds and never depend on real wall-clock
    time. The fake manager's ``list_active()`` drains on the first
    sleep tick so the wait resolves early.
    """
    clock = _FakeClock()
    sleep_calls: list[float] = []
    active_records: list[ProcessRecord] = []
    record = ProcessRecord(
        pid=_PID,
        pgid=_PGID,
        command=("fake",),
        cwd=None,
        started_at=datetime.now(tz=UTC),
        status=ProcessStatus.RUNNING,
        label="invoke:fake",
    )
    active_records.append(record)

    def _fake_clock() -> float:
        return clock.now()

    def _fake_sleep(s: float) -> None:
        sleep_calls.append(s)
        if active_records:
            active_records.clear()

    manager = FakeProcessManager()
    manager._active_records = active_records
    exit_calls: list[tuple[int, ...]] = []
    _, dispatcher = _build_dispatcher(
        process_manager=manager,
        hard_exit=cast("Callable[[int], None]", lambda code: exit_calls.append((code,))),
        clock=cast("Callable[[], float]", _fake_clock),
        sleep=cast("Callable[[float], None]", _fake_sleep),
    )
    dispatcher.begin_interrupt(block=True, grace_period_s=_INVOKE_GRACE)
    assert len(sleep_calls) >= 1
    # AC-07 contract: every sleep is bounded by BOTH poll_interval_s
    # AND grace_period_s. The unfixed code used a hard-coded 0.01s
    # clamp, so this assertion is contract-level (does NOT codify
    # the implementation). The bound is min(poll, grace).
    import_dispatcher_poll = 0.2  # SIGINT_PROGRESS_POLL_INTERVAL_SECONDS
    expected_bound = min(import_dispatcher_poll, _INVOKE_GRACE)
    assert sleep_calls[0] <= expected_bound
    assert sleep_calls[0] <= _INVOKE_GRACE


def test_dispatcher_block_wait_sleep_never_exceeds_remaining_deadline() -> None:
    """AC-02 (step 2): the block-wait sleep bound must be
    ``min(poll_interval_s, max(deadline - clock(), 0.0))``. Every
    sleep is bounded by BOTH ``poll_interval_s`` AND the remaining
    time before the deadline.

    With ``grace_period_s=1.0`` and ``poll_interval_s=0.2``, the
    contract pins:

    * every sleep call is ``<= poll_interval_s``,
    * every sleep call is ``<= grace_period_s``, and
    * the total sleep count is in ``[4, 6]`` (not 50-100).

    The unfixed code calls
    ``self.sleep(min(self.poll_interval_s, 0.01))`` every tick, so
    ``sleep_calls[0] == 0.01`` and the count is ~100. The test
    FAILS under the unfixed code (count is out of range,
    ``sleep_calls[0] <= 0.01`` is below ``poll_interval_s``) and
    PASSES under the fixed code (sleep count is ~5).
    """
    manager = FakeProcessManager()
    # Record that NEVER drains; the dispatcher must rely on the deadline.
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")
    clock = _FakeClock()
    sleep_calls: list[float] = []
    exit_calls: list[tuple[int, ...]] = []

    def _fake_sleep(s: float) -> None:
        sleep_calls.append(s)
        clock.advance(s)

    _, dispatcher = _build_dispatcher(
        process_manager=manager,
        hard_exit=cast("Callable[[int], None]", lambda code: exit_calls.append((code,))),
        poll_interval_s=0.2,
        clock=cast("Callable[[], float]", clock.now),
        sleep=cast("Callable[[float], None]", _fake_sleep),
    )
    dispatcher.begin_interrupt(block=True, grace_period_s=1.0)

    # Contract: every sleep is bounded by BOTH poll_interval_s AND
    # grace_period_s. This is contract-level (not implementation-level)
    # and does not assume the specific ``min()`` formula.
    assert sleep_calls, "block-wait must call sleep at least once"
    for s in sleep_calls:
        assert s <= 0.2, f"sleep {s} exceeds poll_interval_s 0.2"
        assert s <= 1.0, f"sleep {s} exceeds grace_period_s 1.0"
    # Sleep count must reflect grace / poll, not grace / 0.01.
    assert 4 <= len(sleep_calls) <= 6, (
        f"sleep count {len(sleep_calls)} out of expected range [4, 6]; sleep_calls={sleep_calls}"
    )


def test_dispatcher_block_wait_does_not_sleep_when_already_empty() -> None:
    """Test 2: when the manager's active list is already empty, the
    dispatcher must short-circuit and NOT call sleep. This is the
    canonical early-exit path for the ``block=True`` flow when no
    process is registered as active.
    """
    clock = _FakeClock()
    sleep_calls: list[float] = []

    manager = FakeProcessManager()
    _, dispatcher = _build_dispatcher(
        process_manager=manager,
        hard_exit=cast("Callable[[int], None]", lambda _c: None),
        clock=cast("Callable[[], float]", clock.now),
        sleep=cast("Callable[[float], None]", sleep_calls.append),
    )
    dispatcher.begin_interrupt(block=True, grace_period_s=_INVOKE_GRACE)
    assert sleep_calls == []


def test_dispatcher_early_escalation_uses_injected_clock_for_deadline() -> None:
    """Test 3: ``run_early_escalation_poll`` must read the dispatcher's
    ``clock`` field for its deadline and call ``self.sleep`` (not
    ``time.sleep``) for the per-iteration wait. Pins the seam for the
    early-escalation path so a regression to ``time.monotonic()`` /
    ``time.sleep()`` breaks this test.
    """
    clock = _FakeClock()
    sleep_calls: list[float] = []

    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")

    def _fake_sleep(s: float) -> None:
        sleep_calls.append(s)

    dispatcher = InterruptDispatcher(
        controller=InterruptController(
            shutdown_all=lambda _g: None,
            shutdown_all_for_label=lambda _l, _g: None,
        ),
        process_manager=manager,
        hard_exit=cast("Callable[[int], None]", lambda _c: None),
        poll_interval_s=_POLL_INTERVAL,
        hard_kill_budget_s=_QUICK_BUDGET,
        clock=cast("Callable[[], float]", clock.now),
        sleep=cast("Callable[[float], None]", _fake_sleep),
    )
    dispatcher.run_early_escalation_poll(max_wait_s=_QUICK_BUDGET)
    assert sleep_calls, "sleep must be called by run_early_escalation_poll"
    assert sleep_calls[0] == _POLL_INTERVAL


def test_dispatcher_constructor_rejects_clock_returning_non_float() -> None:
    """Test 4: ``__post_init__`` must raise ``RuntimeError`` if the
    ``clock`` field returns a non-float. The clock() call here is the
    only place we can detect a malformed injection at construction
    time, before ``begin_interrupt`` runs.
    """
    manager = FakeProcessManager()
    with pytest.raises(RuntimeError):
        InterruptDispatcher(
            controller=InterruptController(shutdown_all=lambda _g: None),
            process_manager=manager,
            hard_exit=cast("Callable[[int], None]", lambda _c: None),
            poll_interval_s=0.01,
            hard_kill_budget_s=0.05,
            clock=cast("Callable[[], float]", lambda: "1.0"),
            sleep=cast("Callable[[float], None]", lambda _s: None),
        )


def test_dispatcher_factory_defaults_to_time_monotonic() -> None:
    """Test 5: when the factory is called without explicit ``clock`` /
    ``sleep`` kwargs, the dispatcher must default ``clock`` to
    ``time.monotonic`` and ``sleep`` to ``time.sleep``. Identity check
    (``is``) confirms the module-level reference is the same as the
    default — not a wrapper.
    """
    dispatcher = dispatcher_from_process_manager(
        hard_exit=cast("Callable[[int], None]", lambda _c: None),
    )
    assert dispatcher.clock is time.monotonic
    assert dispatcher.sleep is time.sleep


def test_dispatcher_early_escalation_poll_sleeps_before_matched_check() -> None:
    """Test 6 (NEW): the per-iteration order in
    ``run_early_escalation_poll`` MUST be ``sleep(poll)`` then
    ``list_active()`` (matched check), not the reverse. Pinning the
    order preserves the existing semantics where the first poll
    happens AFTER one sleep tick (so a freshly-started process has
    time to register).
    """
    clock = _FakeClock()
    event_order: list[str] = []

    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")

    original_list_active = manager.list_active

    def _recording_list_active() -> list[ProcessRecord]:
        event_order.append("list_active")
        return original_list_active()

    manager.list_active = cast("Any", _recording_list_active)

    def _fake_sleep(s: float) -> None:
        event_order.append(f"sleep({s})")

    dispatcher = InterruptDispatcher(
        controller=InterruptController(
            shutdown_all=lambda _g: None,
            shutdown_all_for_label=lambda _l, _g: None,
        ),
        process_manager=manager,
        hard_exit=cast("Callable[[int], None]", lambda _c: None),
        poll_interval_s=_POLL_INTERVAL,
        hard_kill_budget_s=_QUICK_BUDGET,
        clock=cast("Callable[[], float]", clock.now),
        sleep=cast("Callable[[float], None]", _fake_sleep),
    )
    dispatcher.run_early_escalation_poll(max_wait_s=_QUICK_BUDGET)
    sleep_indices = [i for i, e in enumerate(event_order) if e.startswith("sleep(")]
    list_indices = [i for i, e in enumerate(event_order) if e == "list_active"]
    assert sleep_indices and list_indices
    assert sleep_indices[0] < list_indices[0], (
        f"sleep(poll) MUST come before the first list_active check; got event_order={event_order}"
    )


def test_dispatcher_waits_until_empty_or_escalates_when_stuck() -> None:
    """ROOT-CAUSE FIX PIN: when ``block=True`` and the manager's active
    records never drain, the dispatcher must ESCALATE (via
    ``force_exit``) after the grace deadline, not return silently.
    This is the canonical pin for the PROMPT's 'frozen pipeline after
    Ctrl+C' failure mode — production code must call
    ``self.force_exit(bridge_pids=...)`` with the still-active
    records' pids.
    """
    clock = _FakeClock()
    sleep_calls: list[float] = []
    exit_calls: list[tuple[int, ...]] = []
    kill_calls: list[tuple[int, int]] = []

    manager = FakeProcessManager()
    # The record NEVER drains — fake_sleep does NOT touch _active_records.
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")

    def _fake_sleep(s: float) -> None:
        sleep_calls.append(s)
        # Advance the clock past the grace deadline to escape the wait loop.
        clock.advance(s)

    _, dispatcher = _build_dispatcher(
        process_manager=manager,
        hard_exit=cast("Callable[[int], None]", lambda code: exit_calls.append((code,))),
        kill_process_group=cast(
            "Callable[[int, int], None]", lambda pgid, sig: kill_calls.append((pgid, sig))
        ),
        clock=cast("Callable[[], float]", clock.now),
        sleep=cast("Callable[[float], None]", _fake_sleep),
    )
    dispatcher.begin_interrupt(block=True, grace_period_s=0.05)
    # force_exit was called with INTERRUPT_EXIT_CODE
    assert exit_calls == [(INTERRUPT_EXIT_CODE,)]
    # force_exit routed through controller.force_interrupt →
    # shutdown_all(0). FakePM.shutdown_all(0) records kills in
    # kill_process_group_calls to mirror the real ProcessManager's
    # escalation (AC-11 contract: manager.shutdown_all(0) is the
    # only kill path).
    assert (_PGID, signal.SIGKILL) in manager.kill_process_group_calls


def test_begin_interrupt_with_slow_body_still_escalates_after_body_returns() -> None:
    """NEW BEHAVIOR PIN: when ``begin_interrupt``'s body itself
    (the controller's ``record_interrupt`` + ``shutdown_all_for_label``)
    takes a long time to return, the dispatcher's
    ``_wait_for_list_active_empty`` escalation still fires once
    the body returns and the new grace deadline (measured from
    when ``_wait_for_list_active_empty`` starts) elapses.

    The existing tests
    (``test_dispatcher_waits_until_empty_or_escalates_when_stuck``,
    ``test_dispatcher_block_wait_sleep_never_exceeds_remaining_deadline``)
    assume the body returns instantaneously; they cannot detect
    a regression where the dispatcher forgets to subtract body
    time from the grace budget. The new test pins the body-time
    invariant via a ``threading.Event`` coordination primitive
    (the same pattern as
    ``test_dispatcher_begin_interrupt_block_true_blocks_until_list_active_empty``).

    Mechanism:

    1. Build a ``FakeProcessManager`` with one active record that
       NEVER drains (the FakeProcessManager default).
    2. Build a ``_FakeClock`` and a ``fake_sleep`` that advances
       the clock on every call.
    3. Define ``body_event`` and ``body_started`` threading
       events. Build ``_slow_record_interrupt`` that sets
       ``body_started`` and blocks on ``body_event.wait``.
    4. Build the dispatcher with ``poll_interval_s=0.001`` and
       ``grace_period_s=0.05`` and the ``_slow_record_interrupt``
       injection.
    5. Start a worker thread that calls
       ``dispatcher.begin_interrupt(block=True, grace_period_s=0.05)``.
       The thread blocks inside ``_slow_record_interrupt``.
    6. Wait on ``body_started`` to confirm the body is blocked.
    7. Advance the clock past the original grace deadline
       (0.5s) — the dispatcher's escalation would fire if the
       body had returned; the worker is still inside
       ``_slow_record_interrupt``.
    8. Set ``body_event`` so the body returns. The worker
       continues through ``shutdown_all_for_label`` into
       ``_wait_for_list_active_empty``.
    9. Join the worker thread.
    10. Assert ``hard_exit`` was called exactly once
        (idempotency survives body delay) and
        ``shutdown_all_for_label`` was called with the expected
        label + grace.
    """
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")

    clock = _FakeClock()
    sleep_calls: list[float] = []
    exit_calls: list[tuple[int, ...]] = []
    kill_calls: list[tuple[int, int]] = []

    body_event = threading.Event()
    body_started = threading.Event()

    def _slow_record_interrupt() -> None:
        body_started.set()
        body_event.wait(timeout=1.0)

    def _fake_sleep(s: float) -> None:
        sleep_calls.append(s)
        clock.advance(s)

    dispatcher = dispatcher_from_process_manager(
        process_manager=manager,
        hard_exit=cast("Callable[[int], None]", lambda code: exit_calls.append((code,))),
        kill_process_group=cast(
            "Callable[[int, int], None]", lambda pgid, sig: kill_calls.append((pgid, sig))
        ),
        record_interrupt=_slow_record_interrupt,
        poll_interval_s=0.001,
        hard_kill_budget_s=0.05,
        clock=cast("Callable[[], float]", clock.now),
        sleep=cast("Callable[[float], None]", _fake_sleep),
    )

    def _worker() -> None:
        dispatcher.begin_interrupt(block=True, grace_period_s=0.05)

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()

    # The body must be blocked in _slow_record_interrupt before we
    # advance the clock.
    assert body_started.wait(timeout=0.5), (
        "worker thread did not enter _slow_record_interrupt in 0.5s"
    )

    # Advance the clock past the original grace deadline while the
    # body is still blocked.
    clock.advance(0.5)

    # Release the body. The worker continues through
    # shutdown_all_for_label into _wait_for_list_active_empty.
    body_event.set()

    # The worker thread should complete (the dispatcher escalates
    # after the body returns and the new grace deadline elapses).
    worker.join(timeout=1.0)
    assert not worker.is_alive(), (
        f"worker thread did not complete in 1.0s; "
        f"sleep_calls={sleep_calls}, exit_calls={exit_calls}"
    )

    # hard_exit called exactly once (idempotency survives body delay).
    assert exit_calls == [(INTERRUPT_EXIT_CODE,)], (
        f"hard_exit should be called exactly once; got {exit_calls}"
    )

    # The body did call the label-targeted shutdown before
    # _wait_for_list_active_empty was entered.
    assert manager.shutdown_all_for_label_calls == [("invoke:", 0.05)], (
        f"shutdown_all_for_label should be called once with the dispatcher's "
        f"kill_label and grace_period_s; got {manager.shutdown_all_for_label_calls}"
    )


def test_handle_keyboard_interrupt_force_kill_handler_restores_previous() -> None:
    """``handle_keyboard_interrupt`` must install and then RESTORE the
    previous SIGINT handler when called with injected signal_getter/
    signal_setter. A regression that fails to restore would leave the
    test process in a state where Ctrl+C kills the test runner."""
    manager = FakeProcessManager()
    _, dispatcher = _build_dispatcher(process_manager=manager)
    set_calls: list[tuple[int, object]] = []
    previous_handler = object()

    def _fake_getsignal(signum: int) -> object:
        return previous_handler

    def _fake_set(signum: int, handler: object) -> object:
        set_calls.append((signum, handler))
        return handler

    handle_keyboard_interrupt(
        monitor_stop=None,
        dispatcher=dispatcher,
        signal_getter=cast("Callable[[int], object]", _fake_getsignal),
        signal_setter=cast("Callable[[int, object], object]", _fake_set),
    )
    assert len(set_calls) == 4
    assert set_calls[0][0] == _SIGINT
    assert set_calls[1][0] == _SIGTERM
    assert set_calls[2][0] == _SIGINT
    assert set_calls[3][0] == _SIGTERM
    assert set_calls[2][1] is previous_handler


class _CancellableTask:
    """Minimal task-like object whose ``cancel()`` records the call.

    Mirrors the duck-typed surface ``install_signal_handlers`` uses:
    it calls ``root_task.cancel()`` after the first SIGINT handler
    fires. No real asyncio event loop or coroutine is involved.
    """

    def __init__(self) -> None:
        self.cancel_calls: int = 0

    def cancel(self) -> None:
        self.cancel_calls += 1


class _HandlerCapturingLoop(asyncio.AbstractEventLoop):
    """A minimal asyncio loop wrapper that captures ``add_signal_handler`` calls.

    The production ``install_signal_handlers`` calls
    ``loop.add_signal_handler(SIGINT, callback)`` exactly once for the
    first SIGINT, then again after the first SIGINT to install the
    second-SIGINT handler. We capture each call so the test can invoke
    the first/second handler directly without a real signal.

    Implements only the methods ``install_signal_handlers`` exercises;
    any other call raises ``NotImplementedError`` so the test fails
    fast if a regression exercises a new loop API.
    """

    def __init__(self) -> None:
        self._handlers: list[Callable[[], None]] = []

    def add_signal_handler(self, sig: int, callback: Callable[[], None], *args: object) -> object:
        del sig, args
        self._handlers.append(callback)
        return None

    def remove_signal_handler(self, sig: int) -> bool:
        del sig
        return True

    def close(self) -> None:  # pragma: no cover - never invoked in tests
        return

    def run_forever(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def run_until_complete(self, future: object) -> object:  # pragma: no cover
        raise NotImplementedError

    def is_closed(self) -> bool:  # pragma: no cover
        return True

    def is_running(self) -> bool:  # pragma: no cover
        return False

    def get_debug(self) -> bool:  # pragma: no cover
        return False

    def set_debug(self, enabled: bool) -> None:  # pragma: no cover
        del enabled

    def default_exception_handler(self, context: object) -> None:  # pragma: no cover
        del context

    def call_exception_handler(self, context: object) -> None:  # pragma: no cover
        del context

    def get_exception_handler(self) -> object:  # pragma: no cover
        return None

    def set_exception_handler(self, handler: object) -> None:  # pragma: no cover
        del handler

    def time(self) -> float:  # pragma: no cover
        return 0.0

    def call_later(  # pragma: no cover
        self, delay: float, callback: Callable[[], None], *args: object
    ) -> object:
        del delay, callback, args
        return None

    def call_soon(  # pragma: no cover
        self, callback: Callable[[], None], *args: object
    ) -> object:
        del callback, args
        return None

    def create_task(  # pragma: no cover
        self, coro: object, *, name: str | None = None
    ) -> object:
        raise NotImplementedError

    def create_future(self) -> object:  # pragma: no cover
        raise NotImplementedError


@pytest.mark.subprocess_e2e
def test_async_first_sigint_propagates_kill_label_to_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The async SIGINT path must call the controller's begin_interrupt
    with ``kill_label='invoke:'`` (the dispatcher's default), so the
    label-targeted shutdown_all_for_label path is taken on the first
    SIGINT.

    The first-SIGINT handler dispatches ``begin_interrupt`` +
    ``run_early_escalation_poll`` via ``loop.run_in_executor``. The
    loop shim runs the executor body synchronously so the test can
    observe the controller's ``shutdown_all_for_label`` call.
    """
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")
    _, dispatcher = _build_dispatcher(process_manager=manager)

    monkeypatch.setattr("ralph.interrupt.asyncio_bridge.get_process_manager", lambda: manager)

    bridge = SignalBridge()
    loop = _SyncExecutorHandlerLoop()
    root_task = _CancellableTask()
    install_signal_handlers(loop, root_task, bridge, dispatcher)
    assert len(loop._handlers) == 2
    loop._handlers[0]()
    assert root_task.cancel_calls == 1
    assert manager.shutdown_all_for_label_calls == [("invoke:", _FAKE_DEFAULT_GRACE)]


class _SyncExecutorHandlerLoop(_HandlerCapturingLoop):
    """Variant of ``_HandlerCapturingLoop`` that exposes a
    synchronous ``run_in_executor`` so the first-SIGINT handler's
    executor dispatch can be exercised end-to-end. The returned
    handle supports ``add_done_callback`` (invoked synchronously).
    """

    def run_in_executor(self, executor: object, fn: object, *args: object) -> object:
        fn(*args)
        return _SyncDoneHandle()


class _SyncDoneHandle:
    """Minimal handle with ``add_done_callback`` for AC-04-style tests."""

    def add_done_callback(self, callback: object) -> None:
        callback(self)

    def cancelled(self) -> bool:
        return False

    def exception(self) -> object:
        return None


def test_cli_catch_uses_dispatcher_with_block_true() -> None:
    """The CLI catch in ``cli/commands/run.py`` must call
    ``dispatcher_from_process_manager`` and invoke ``begin_interrupt``
    with ``block=True`` so the agent's process group is SIGTERMed
    even when the interrupt is raised outside the pipeline loop.

    Pins the integration contract: when the CLI KeyboardInterrupt
    catch runs, the resulting dispatcher's ``begin_interrupt`` is
    called with ``block=True``.
    """
    real_factory = dispatcher_from_process_manager
    block_calls: list[bool] = []

    def _spy(**kwargs: object) -> InterruptDispatcher:
        pm = kwargs.get("process_manager")
        dispatcher: InterruptDispatcher = real_factory(process_manager=pm)
        real_begin = dispatcher.begin_interrupt

        class _Wrap:
            def __init__(self, inner: InterruptDispatcher) -> None:
                self._inner = inner

            def __call__(self, *args: object, **kw: object) -> None:
                block_calls.append(bool(kw.get("block", False)))
                real_begin(*args, **kw)

        object.__setattr__(dispatcher, "begin_interrupt", _Wrap(dispatcher))
        return dispatcher

    original_factory: object = getattr(
        ralph_cli_run_module, "dispatcher_from_process_manager", None
    )
    ralph_cli_run_module.dispatcher_from_process_manager = _spy
    try:
        manager = FakeProcessManager()
        spy_dispatcher: InterruptDispatcher = _spy(process_manager=manager)
        spy_dispatcher.begin_interrupt(block=True)
    finally:
        ralph_cli_run_module.dispatcher_from_process_manager = original_factory
    assert block_calls == [True]


def test_async_install_signal_handlers_accepts_dispatcher_positionally() -> None:
    """Passing an InterruptDispatcher as the 4th positional argument
    must wire it through, not the controller fallback."""
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")
    _, dispatcher = _build_dispatcher(process_manager=manager)

    bridge = SignalBridge()
    loop = _HandlerCapturingLoop()
    root_task = _CancellableTask()
    install_signal_handlers(loop, root_task, bridge, dispatcher)
    assert len(loop._handlers) == 2
    assert dispatcher.controller.shutdown_all_for_label is not None


def test_async_dispatcher_synthesis_threads_kill_process_group_and_hard_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PA-019 PINNED CONTRACT: when a raw InterruptController is
    passed to ``install_signal_handlers``, the synthesised dispatcher
    must forward ``kill_process_group`` and ``hard_exit`` so the
    controller's injected exit callable is the one invoked on
    _second_sigint — NOT the factory default ``os._exit``."""
    kill_calls: list[tuple[int, int]] = []
    exit_calls: list[tuple[int, ...]] = []
    os_exit_calls: list[tuple[int, ...]] = []

    def _record_kill(pid: int, sig: int) -> None:
        kill_calls.append((pid, sig))

    def _record_exit(code: int) -> None:
        exit_calls.append((code,))

    def _fake_os_exit(code: int) -> None:
        os_exit_calls.append((code,))
        raise SystemExit(code)

    monkeypatch.setattr(os, "_exit", _fake_os_exit)

    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")
    controller = controller_from_process_manager(
        process_manager=manager,
        kill_process_group=_record_kill,
        hard_exit=_record_exit,
    )

    bridge = SignalBridge()
    loop = _SyncExecutorHandlerLoop()
    root_task = _CancellableTask()
    install_signal_handlers(loop, root_task, bridge, controller)
    assert len(loop._handlers) == 2
    loop._handlers[0]()
    # Both SIGINT and SIGTERM were escalated to the force-exit
    # handler — AC-01 mixed-signal contract.
    assert len(loop._handlers) == 4
    bridge._interrupt_count = 2
    with contextlib.suppress(SystemExit):
        loop._handlers[2]()
    assert exit_calls == [(INTERRUPT_EXIT_CODE,)]
    assert os_exit_calls == []


@pytest.mark.subprocess_e2e
def test_async_install_signal_handlers_3arg_call_path_controller_none_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PA-017 PINNED CONTRACT: the 3-arg call path (``controller=None``)
    must synthesize a working dispatcher without raising. Mirrors
    ``test_user_interrupt.py:124``'s calling convention.

    The 3-arg path builds an internal dispatcher via
    ``dispatcher_from_process_manager`` (without an explicit
    process_manager), which routes through the singleton. The
    test monkeypatches the singleton getter so the test does NOT
    touch the real ProcessManager; the singleton is replaced with
    a FakeProcessManager that owns the active record.
    """
    os_exit_calls: list[tuple[int, ...]] = []
    manager = FakeProcessManager()
    manager.add_active(pid=_PID, pgid=_PGID, label="invoke:fake")

    def _fake_os_exit(code: int) -> None:
        os_exit_calls.append((code,))
        raise SystemExit(code)

    monkeypatch.setattr(os, "_exit", _fake_os_exit)
    monkeypatch.setattr("ralph.interrupt.asyncio_bridge.get_process_manager", lambda: manager)

    bridge = SignalBridge()
    loop = _SyncExecutorHandlerLoop()
    root_task = _CancellableTask()
    install_signal_handlers(loop, root_task, bridge)
    assert len(loop._handlers) == 2
    loop._handlers[0]()
    # Both SIGINT and SIGTERM were escalated to the force-exit
    # handler — AC-01 mixed-signal contract.
    assert len(loop._handlers) == 4
    bridge._interrupt_count = 2
    with contextlib.suppress(SystemExit):
        loop._handlers[2]()
    assert os_exit_calls == [(INTERRUPT_EXIT_CODE,)]


def test_run_shutdown_block_does_not_sigkill_alive_but_zero_cpu_long_running_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TDD-first regression pin for the I/O-bound alive-but-zero-CPU bug.

    The PROMPT reports 'broken at times... when the task is long
    running'. The architecture-internal root cause: the production seam
    in ``run_shutdown_block`` (the helper used by both the SYNC
    ``handle_keyboard_interrupt`` and the asyncio
    ``install_signal_handlers`` paths) deliberately routes through
    ``run_early_escalation_poll`` in a daemon thread, which uses
    CPU-progress detection. An I/O-bound long-running agent that is
    alive and gracefully shutting down (writing a checkpoint,
    releasing a lock, draining a queue) frequently has
    ``current_cpu == previous_cpu`` across two consecutive polls, so
    ``_records_show_no_progress`` returns True and the daemon thread
    SIGKILLs the agent prematurely.

    The fix routes the production seam through
    ``begin_interrupt(block=True)``, which uses the dispatcher's
    liveness-based ``_wait_for_list_active_empty`` to wait for the
    records to drain naturally. The alive-but-zero-CPU agent is given
    the full ``grace_period_s`` to die on its own; no SIGKILL is
    sent unless the grace deadline elapses with records still
    active.

    Test scenario:

    1. Build a ``FakeProcessManager`` with
       ``default_grace_period_s=1.0`` and add one active record
       ``(pid=201, pgid=9999, label='invoke:fake')``.
    2. Build a ``_FakeClock`` and a fake sleep that advances the
       clock by 0.001 on every call AND calls ``manager.drain()``
       after exactly 3 iterations (the agent's natural completion).
    3. Patch psutil so ``_psutil_pid_cpu_time`` returns 0.0 on
       every call (the agent is alive but not using the CPU). Patch
       ``os.kill`` so ``_pid_is_alive`` returns True (the agent is
       alive at the OS level).
    4. Build a dispatcher via the factory with ``poll_interval_s=0.001``
       and the fake clock + fake sleep.
    5. Call ``run_shutdown_block(dispatcher, grace_period_s=1.0,
       join_timeout_s=0.05)`` from the canonical production seam.
    6. Assert ``manager.kill_process_group_calls == []`` — the
       alive-but-zero-CPU agent is NOT SIGKILLed.
    7. Assert ``manager._active_records == []`` — the records were
       drained naturally by the fake sleep, not killed by
       ``force_exit``.

    Behaviour:

    * Under the OLD code, the daemon thread SIGKILLs the agent at
      iteration 2 (two consecutive 0-CPU polls trigger
      ``_records_show_no_progress`` to return True; the daemon
      thread then calls ``_dispatch_kill`` which records the kill in
      ``kill_process_group_calls``). The assertion FAILS.
    * Under the NEW code, ``begin_interrupt(block=True)`` routes
      through ``_wait_for_list_active_empty`` (liveness-based) which
      waits for the records to drain. The fake sleep drains the
      records at iteration 3; the wait returns. No SIGKILL is sent.
      The assertion PASSES.

    Wall-clock: < 100ms (uses ``_FakeClock`` and a fake sleep; the
    ``join_timeout_s=0.05`` keeps the OLD-code main-thread join
    fast even when the daemon thread races). The test does NOT use
    ``time.sleep`` and does NOT use real subprocesses.
    """
    clock = _FakeClock()
    sleep_iterations = 0
    captured_manager: list[FakeProcessManager] = []

    def _fake_sleep(s: float) -> None:
        nonlocal sleep_iterations
        sleep_iterations += 1
        clock.advance(s)
        if sleep_iterations == 3 and captured_manager:
            captured_manager[0].drain()

    _patch_psutil(monkeypatch, lambda _n: 0.0)
    _patch_pid_alive(monkeypatch, alive=True)

    manager, dispatcher = _build_dispatcher(
        clock=cast("Callable[[], float]", clock.now),
        sleep=cast("Callable[[float], None]", _fake_sleep),
        poll_interval_s=0.001,
    )
    captured_manager.append(manager)
    manager.add_active(pid=201, pgid=9999, label="invoke:fake")
    manager.policy = ProcessManagerPolicy(default_grace_period_s=1.0)

    run_shutdown_block(
        dispatcher,
        grace_period_s=1.0,
        join_timeout_s=0.05,
    )

    # The alive-but-zero-CPU agent is NOT SIGKILLed under the fix.
    assert manager.kill_process_group_calls == [], (
        f"alive-but-zero-CPU agent was SIGKILLed; "
        f"kill_process_group_calls={manager.kill_process_group_calls}"
    )
    # The records were drained naturally by the fake sleep, not killed
    # by force_exit.
    assert manager._active_records == [], (
        f"records should be drained naturally by the fake sleep; "
        f"got _active_records={manager._active_records}"
    )
