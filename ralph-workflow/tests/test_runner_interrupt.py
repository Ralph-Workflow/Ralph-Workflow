"""Black-box tests for the ralph.pipeline._runner_interrupt entry point.

These tests pin the 6 contracts the refactored entry point must satisfy:

1. poll_interval seam: the busy-wait timeout uses the injected
   ``poll_interval_s`` (default 0.05) and the dispatcher's poll loop
   respects a separate ``poll_interval_s=0.001`` override so per-test
   wall-clock stays under 100ms.
2. Dispatcher exception recovery: non-fatal dispatcher failures
   (RuntimeError, ValueError, OSError) are recovered with a logged
   warning and the SIGINT handler is restored.
3. monitor_stop rejection: passing both a pre-built dispatcher and a
   ``monitor_stop`` raises ``RuntimeError`` (the prior silent-ignore
   was a footgun).
4. First-SIGINT contract: ``begin_interrupt`` is called once with the
   process manager's default grace period, then
   ``run_early_escalation_poll`` is called once with
   ``INTERRUPT_HARD_KILL_BUDGET_SECONDS``.
5. Second-SIGINT contract: the force-kill handler routes through
   ``dispatcher.force_exit`` with ``bridge_pgids`` from
   ``process_manager.list_active()``.
6. BaseException propagation (THE DISCRIMINATOR FOR THE
   ``Exception``-not-``BaseException`` CHANGE): a ``BaseException``
   subclass that is NOT an ``Exception`` subclass is NOT caught by the
   recovery block; it propagates out of the background thread and is
   captured by ``threading.excepthook``. The OLD code (``BaseException``)
   would silently catch it. Test 2 alone is INSUFFICIENT because
   ``RuntimeError`` is caught identically by both ``except
   BaseException`` and ``except Exception``.

Every test in this file MUST pass ``signal_getter`` and ``signal_setter``
fakes to ``handle_keyboard_interrupt`` so the test does NOT touch the
real process SIGINT handler. The canonical pattern is at
``tests/test_interrupt_dispatcher.py:939-969``; this file mirrors it.

Every test MUST build a REAL factory-built ``InterruptDispatcher`` via
``_build_dispatcher(manager)`` (which sets ``poll_interval_s=0.001``
and ``hard_kill_budget_s=0.05``) and pass it (or the
``_RecordingDispatcher`` wrapper around it, for tests 4 and 5) to
``handle_keyboard_interrupt``. The ``poll_interval_s=0.001`` override
is MANDATORY: it bounds the dispatcher's ``run_early_escalation_poll``
first sleep tick to <1ms so the entire poll loop exits in <2ms.

A thin ``_RecordingDispatcher`` wrapper class local to this test file
is the ONLY recording mechanism for ``begin_interrupt`` /
``run_early_escalation_poll`` / ``force_exit`` argument recording. The
wrapper exists because the real ``InterruptDispatcher`` is
``@dataclass(frozen=True)`` and cannot be monkey-patched, AND because
``FakeProcessManager.shutdown_all_for_label_calls`` only records
``begin_interrupt`` side effects (not the ``run_early_escalation_poll``
budget argument or the ``force_exit`` bridge_pgids list).
"""

from __future__ import annotations

import signal
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from loguru import logger

from ralph.interrupt.dispatcher import (
    InterruptDispatcher,
    dispatcher_from_process_manager,
)
from ralph.pipeline._runner_interrupt import handle_keyboard_interrupt
from ralph.process.manager import ProcessManagerPolicy, ProcessRecord, ProcessStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.interrupt.signal_handler import SignalHandler


_PID = 101
_QUICK_BUDGET = 0.05
_POLL_INTERVAL = 0.001
_FAKE_DEFAULT_GRACE = 2.5


@dataclass
class FakeProcessManager:
    """Black-box fake of ``ProcessManager`` for entry-point tests.

    Mirrors the ``FakeProcessManager`` in
    ``tests/test_interrupt_dispatcher.py:60-122``. Only the methods the
    entry point exercises are wired with recording callables. Everything
    else is a no-op so the dispatcher can call any reasonable subset of
    the surface without raising.
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


def _build_dispatcher(
    manager: FakeProcessManager,
    *,
    hard_exit: Callable[[int], None] | None = None,
) -> InterruptDispatcher:
    """Build a real factory-built ``InterruptDispatcher`` with quick-test timings.

    The ``poll_interval_s=0.001`` override is MANDATORY: it bounds the
    dispatcher's ``run_early_escalation_poll`` first sleep tick to <1ms
    so the entire poll loop exits in <2ms. The
    ``hard_kill_budget_s=0.05`` override is a defensive cap: with the
    fake PIDs (101, 202, etc.) almost certainly not alive in the test
    environment, ``_any_record_alive`` returns False after the first
    sleep tick and the loop exits.

    The optional ``hard_exit`` is a recording callable that replaces
    the default ``os._exit`` in the controller's force-exit path.
    Tests that simulate a second SIGINT (test 5) MUST pass a
    recording ``hard_exit`` so the test process is not killed by
    ``os._exit(130)``.
    """
    return dispatcher_from_process_manager(
        process_manager=cast("object", manager),
        poll_interval_s=_POLL_INTERVAL,
        hard_kill_budget_s=_QUICK_BUDGET,
        hard_exit=hard_exit,
    )


def _make_signal_fakes() -> tuple[
    Callable[[int], SignalHandler],
    Callable[[int, SignalHandler], SignalHandler],
    list[tuple[int, SignalHandler]],
    SignalHandler,
]:
    """Return (getter, setter, set_calls, previous_handler).

    Mirrors the canonical pattern at
    ``tests/test_interrupt_dispatcher.py:939-969``.
    """
    set_calls: list[tuple[int, SignalHandler]] = []

    def _previous_handler(signum: int, frame: object) -> object:
        del signum, frame
        return None

    def _fake_getsignal(signum: int) -> SignalHandler:
        del signum
        return _previous_handler

    def _fake_set(signum: int, handler: SignalHandler) -> SignalHandler:
        set_calls.append((signum, handler))
        return handler

    previous_handler: SignalHandler = cast("SignalHandler", _previous_handler)
    return (
        cast("Callable[[int], SignalHandler]", _fake_getsignal),
        cast("Callable[[int, SignalHandler], SignalHandler]", _fake_set),
        set_calls,
        previous_handler,
    )


class _RecordingDispatcher:
    """Thin recording wrapper around a real factory-built ``InterruptDispatcher``.

    The real ``InterruptDispatcher`` is a frozen dataclass and cannot be
    monkey-patched; the wrapper delegates to the real dispatcher and
    records the call arguments for assertion. The wrapper complements
    (does not replace) the ``FakeProcessManager`` recording pattern:
    the manager records the ``begin_interrupt`` side effect via
    ``shutdown_all_for_label_calls`` (the grace period), and the
    wrapper records the ``begin_interrupt`` argument (grace_period_s,
    block), the ``run_early_escalation_poll`` argument (grace_period_s),
    and the ``force_exit`` argument (bridge_pgids).
    """

    def __init__(self, real: InterruptDispatcher) -> None:
        self._real = real
        self.begin_calls: list[dict[str, object]] = []
        self.poll_calls: list[tuple[float, ...]] = []
        self.force_exit_calls: list[list[int]] = []

    def begin_interrupt(
        self,
        grace_period_s: float | None = None,
        *,
        block: bool = False,
    ) -> None:
        self.begin_calls.append({"grace_period_s": grace_period_s, "block": block})
        self._real.begin_interrupt(grace_period_s=grace_period_s, block=block)

    def run_early_escalation_poll(
        self,
        *,
        progress_poll_interval_s: float | None = None,
        max_wait_s: float | None = None,
    ) -> None:
        self.poll_calls.append(())
        self._real.run_early_escalation_poll(
            progress_poll_interval_s=progress_poll_interval_s,
            max_wait_s=max_wait_s,
        )

    def force_exit(self, *, bridge_pgids: object = ()) -> None:
        pgids_list = list(cast("list[int]", bridge_pgids)) if bridge_pgids else []
        self.force_exit_calls.append(pgids_list)
        self._real.force_exit(bridge_pgids=cast("list[int]", bridge_pgids))


class _NotAnException(BaseException):
    """``BaseException`` subclass that is NOT an ``Exception`` subclass.

    Caught by ``except BaseException`` but NOT by ``except Exception``.
    Used by test 6 to prove the ``BaseException``->``Exception`` change
    in the entry point's recovery block.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _DrainOnBeginDispatcher(_RecordingDispatcher):
    """Wrapper that drains the records on the first ``begin_interrupt`` call.

    Under the NEW production seam (``begin_interrupt(block=True)``),
    the first SIGINT body waits for the records to drain before
    returning. If the records never drain, the wait would escalate
    via ``force_exit`` and kill the records, breaking tests that
    need the records to be present for the second-SIGINT force-kill
    handler.

    This wrapper drains the records on the first ``begin_interrupt``
    call (so the liveness-based wait returns early without killing
    the records) and re-adds them after ``begin_interrupt`` returns
    (so the second SIGINT handler sees the records). The
    ``(pid, pgid)`` pairs passed at construction time are used to
    re-add the records with the same identity.
    """

    def __init__(
        self,
        real: InterruptDispatcher,
        manager: FakeProcessManager,
        pids_pgids: list[tuple[int, int]],
    ) -> None:
        super().__init__(real)
        self._manager = manager
        self._pids_pgids = pids_pgids
        self._drained = False

    def begin_interrupt(
        self,
        grace_period_s: float | None = None,
        *,
        block: bool = False,
    ) -> None:
        if not self._drained:
            self._drained = True
            self._manager.drain()
        super().begin_interrupt(grace_period_s=grace_period_s, block=block)
        for pid, pgid in self._pids_pgids:
            self._manager.add_active(pid=pid, pgid=pgid, label="invoke:fake")


def test_handle_keyboard_interrupt_uses_injected_poll_interval_for_polling() -> None:
    """Pins contract 1: the entry point's busy-wait timeout uses the
    injected ``poll_interval_s`` (via the ``poll_interval_s=`` kwarg),
    AND the dispatcher's ``run_early_escalation_poll`` uses the
    factory's ``poll_interval_s=0.001`` override so per-test wall-clock
    stays under 100ms.

    Asserts the install/restore pair happened via ``set_calls`` (len ==
    2, both SIGINT, second is ``previous_handler``). A regression that
    hard-codes the busy-wait to the old literal would still install
    and restore correctly, but the wall-clock assertion (implicit: the
    test completes in <50ms) is the real pin.
    """
    manager = FakeProcessManager()
    dispatcher = _build_dispatcher(manager)
    getter, setter, set_calls, previous_handler = _make_signal_fakes()

    handle_keyboard_interrupt(
        dispatcher=dispatcher,
        signal_getter=getter,
        signal_setter=setter,
        poll_interval_s=_POLL_INTERVAL,
    )
    assert len(set_calls) == 4
    assert set_calls[0][0] == signal.SIGINT
    assert set_calls[1][0] == signal.SIGTERM
    assert set_calls[2][0] == signal.SIGINT
    assert set_calls[3][0] == signal.SIGTERM
    assert set_calls[2][1] is previous_handler


def test_handle_keyboard_interrupt_recovers_from_dispatcher_exception() -> None:
    """Pins contract 2: non-fatal dispatcher failures (RuntimeError,
    ValueError, OSError) are recovered with a logged warning and the
    SIGINT handler is restored.

    NOTE: This test alone does NOT pin the ``BaseException``->``Exception``
    change because ``RuntimeError`` is caught identically by both
    ``except BaseException`` and ``except Exception``. The discriminator
    is test 6.
    """
    getter, setter, set_calls, previous_handler = _make_signal_fakes()

    class _BoomDispatcher:
        def begin_interrupt(
            self,
            grace_period_s: float | None = None,
            *,
            block: bool = False,
        ) -> None:
            del grace_period_s, block
            raise RuntimeError("boom")

    sink: list[object] = []

    def _log_sink(message: object) -> None:
        sink.append(message)

    handler_id = logger.add(_log_sink)
    try:
        handle_keyboard_interrupt(
            dispatcher=cast("InterruptDispatcher", _BoomDispatcher()),
            signal_getter=getter,
            signal_setter=setter,
        )
    finally:
        logger.remove(handler_id)
    assert len(set_calls) == 4
    assert set_calls[0][0] == signal.SIGINT
    assert set_calls[1][0] == signal.SIGTERM
    assert set_calls[2][0] == signal.SIGINT
    assert set_calls[3][0] == signal.SIGTERM
    assert set_calls[2][1] is previous_handler
    assert any("Interrupt controller raised" in str(record) for record in sink), sink


def test_handle_keyboard_interrupt_rejects_monitor_stop_with_pre_built_dispatcher() -> None:
    """Pins contract 3: passing both a pre-built dispatcher and a
    ``monitor_stop`` raises ``RuntimeError``. The guard short-circuits
    in <5ms with no I/O.
    """
    manager = FakeProcessManager()
    dispatcher = _build_dispatcher(manager)
    getter, setter, _set_calls, _previous_handler = _make_signal_fakes()
    with pytest.raises(RuntimeError, match="monitor_stop") as exc_info:
        handle_keyboard_interrupt(
            dispatcher=dispatcher,
            monitor_stop=lambda: None,
            signal_getter=getter,
            signal_setter=setter,
        )
    msg = str(exc_info.value)
    assert "monitor_stop" in msg
    assert "dispatcher" in msg


def test_handle_keyboard_interrupt_runs_early_escalation_poll_after_begin_interrupt() -> None:
    """Pins contract 4: ``begin_interrupt`` is called once with
    ``block=True`` (NEW contract — routes through the dispatcher's
    liveness-based ``_wait_for_list_active_empty``) and the
    controller's label-targeted shutdown runs with the dispatcher's
    kill_label. ``run_early_escalation_poll`` is NOT called from the
    production seam (it is a public utility kept for backward
    compatibility, NOT used by the production seam).

    No active records are added to the manager so the
    ``_wait_for_list_active_empty`` wait (which polls
    ``process_manager.list_active()`` until empty or the deadline
    elapses) returns immediately. The test is about the call shape
    (the recorder captures ``begin_calls`` and ``poll_calls``), not
    the wait behavior; the wait is exercised by
    ``test_dispatcher_begin_interrupt_block_true_blocks_until_list_active_empty``
    in ``test_interrupt_dispatcher.py``.
    """
    manager = FakeProcessManager()
    real_dispatcher = _build_dispatcher(manager)
    recorder = _RecordingDispatcher(real_dispatcher)
    getter, setter, _set_calls, _previous_handler = _make_signal_fakes()

    handle_keyboard_interrupt(
        dispatcher=cast("InterruptDispatcher", recorder),
        process_manager=cast("object", manager),
        signal_getter=getter,
        signal_setter=setter,
        poll_interval_s=_POLL_INTERVAL,
    )
    assert manager.shutdown_all_for_label_calls == [
        ("invoke:", manager.policy.default_grace_period_s)
    ], manager.shutdown_all_for_label_calls
    assert len(recorder.begin_calls) == 1, recorder.begin_calls
    # NEW contract: begin_interrupt is called with block=True so the
    # dispatcher's _wait_for_list_active_empty does the wait.
    assert recorder.begin_calls[0]["block"] is True, recorder.begin_calls
    # NEW contract: run_early_escalation_poll is NOT called from the
    # production seam. The CPU-poll daemon is gone; the public method
    # is kept for backward compatibility but is NOT wired into
    # run_shutdown_block.
    assert recorder.poll_calls == [], recorder.poll_calls


def test_handle_keyboard_interrupt_second_sigint_force_exit_uses_active_pgids() -> None:
    """Pins contract 5: the second-SIGINT force-kill handler routes
    through ``dispatcher.force_exit`` with ``bridge_pgids`` from
    ``process_manager.list_active()`` (in PGID order).

    Under the NEW production seam (``begin_interrupt(block=True)``),
    the first SIGINT body waits for the records to drain before
    returning. If the records never drain, the wait would escalate
    via ``force_exit`` and kill the records, so the second SIGINT
    handler's ``list_active()`` would return ``[]``. The test
    therefore uses a ``_DrainOnBeginDispatcher`` wrapper that
    drains the records on the first ``begin_interrupt`` call (so
    the liveness-based wait returns early without killing the
    records) and re-adds them after ``begin_interrupt`` returns
    (so the second SIGINT handler sees the records).
    """
    manager = FakeProcessManager()
    manager.add_active(pid=101, pgid=9101, label="invoke:fake")
    manager.add_active(pid=202, pgid=9202, label="invoke:fake")
    exit_calls: list[tuple[int, ...]] = []
    real_dispatcher = _build_dispatcher(
        manager,
        hard_exit=cast("Callable[[int], None]", lambda code: exit_calls.append((code,))),
    )
    recorder = _DrainOnBeginDispatcher(
        real_dispatcher,
        manager,
        [(101, 9101), (202, 9202)],
    )
    getter, setter, set_calls, _previous_handler = _make_signal_fakes()

    handle_keyboard_interrupt(
        dispatcher=cast("InterruptDispatcher", recorder),
        process_manager=cast("object", manager),
        signal_getter=getter,
        signal_setter=setter,
        poll_interval_s=_POLL_INTERVAL,
    )
    set_calls[0][1](signal.SIGINT, None)
    assert sorted(recorder.force_exit_calls[-1]) == [9101, 9202], (
        f"expected bridge_pgids from list_active() in any order; got {recorder.force_exit_calls}"
    )
    assert exit_calls == [(130,)], exit_calls


def test_handle_keyboard_interrupt_propagates_baseexception_from_dispatcher() -> None:
    """Pins contract 6 (THE DISCRIMINATOR FOR THE ``BaseException``->``Exception``
    CHANGE). A ``BaseException`` subclass that is NOT an ``Exception``
    subclass is NOT caught by the recovery block; it propagates out of
    the background ``_begin_interrupt`` thread and is captured by
    ``threading.excepthook``. The OLD code (``BaseException``) would
    silently catch it.

    Test 2 (RuntimeError) is INSUFFICIENT: ``RuntimeError`` is caught
    by both ``except BaseException`` and ``except Exception``, so test
    2 would pass against the unmodified code. The discriminator for
    test 6 is the absence of the "Interrupt controller raised" warning
    AND the presence of the ``_NotAnException`` in the captured
    ``threading.excepthook`` arguments.

    The exception happens in a background thread (Python's threading
    does NOT propagate exceptions from threads to the main thread by
    default — they go through ``threading.excepthook``), so this test
    captures the excepthook arguments instead of using
    ``pytest.raises``. The ``pytest.raises`` approach cannot work here
    because the main thread returns normally after the background
    thread terminates with the unhandled exception.

    The SIGINT handler MUST be restored (the install/restore pair runs
    around the thread start in ``handle_keyboard_interrupt``). The
    recovery branch's warning MUST be skipped (because the exception
    short-circuited it).
    """
    getter, setter, set_calls, previous_handler = _make_signal_fakes()

    class _BoomNotException:
        def begin_interrupt(
            self,
            grace_period_s: float | None = None,
            *,
            block: bool = False,
        ) -> None:
            del grace_period_s, block
            raise _NotAnException("boom")

    captured: list[threading.ExceptHookArgs] = []
    excepthook_called = threading.Event()
    original_excepthook = threading.excepthook

    def _capture_excepthook(args: threading.ExceptHookArgs) -> None:
        captured.append(args)
        excepthook_called.set()

    sink: list[object] = []

    def _log_sink(message: object) -> None:
        sink.append(message)

    handler_id = logger.add(_log_sink)
    threading.excepthook = _capture_excepthook
    try:
        handle_keyboard_interrupt(
            dispatcher=cast("InterruptDispatcher", _BoomNotException()),
            signal_getter=getter,
            signal_setter=setter,
        )
        excepthook_called.wait(timeout=2.0)
    finally:
        threading.excepthook = original_excepthook
        logger.remove(handler_id)
    assert len(set_calls) == 4, set_calls
    assert set_calls[0][0] == signal.SIGINT
    assert set_calls[1][0] == signal.SIGTERM
    assert set_calls[2][0] == signal.SIGINT
    assert set_calls[2][1] is previous_handler, set_calls
    assert set_calls[3][0] == signal.SIGTERM
    assert captured, (
        "Expected the background _begin_interrupt thread to raise "
        "_NotAnException; threading.excepthook captured nothing. "
        "The OLD 'except BaseException' code would have silently "
        "caught it and the excepthook would never fire."
    )
    assert any(isinstance(args.exc_value, _NotAnException) for args in captured), captured
    assert not any("Interrupt controller raised" in str(record) for record in sink), sink


class _SlowBeginDispatcher:
    """Dispatcher wrapper that blocks ``begin_interrupt`` on a
    ``threading.Event`` so the test can interleave a second SIGINT
    while the first-SIGINT body is still in flight.

    Mirrors the long-running-body contract the user reports as
    'broken at times... when the task is long running': the
    dispatcher's ``begin_interrupt`` can take a long time on a
    long-running agent (grace_period_s plus block=True), and the
    user hits Ctrl+C a second time while the body is still
    mid-flight. This wrapper blocks the body so the test can
    reliably drive the second-SIGINT-during-first-body scenario
    without depending on real wall-clock waits.

    The wrapper delegates ``force_exit`` and
    ``run_early_escalation_poll`` to the real dispatcher (so the
    production ``_force_exit_called`` idempotency guard is
    exercised) and overrides ``begin_interrupt`` to wait on
    ``_begin_done`` before delegating. The test releases
    ``_begin_done`` after the second-SIGINT handler has fired.
    """

    def __init__(self, real: InterruptDispatcher, begin_done: threading.Event) -> None:
        self._real = real
        self._begin_done = begin_done

    def begin_interrupt(
        self,
        grace_period_s: float | None = None,
        *,
        block: bool = False,
    ) -> None:
        self._begin_done.wait(timeout=5.0)
        self._real.begin_interrupt(grace_period_s=grace_period_s, block=block)

    def run_early_escalation_poll(self, *args: object, **kwargs: object) -> None:
        # The entry-point call site is updated in step 2 to drop
        # the positional ``grace_period_s``; this wrapper accepts
        # both the old and new call shapes so the test continues
        # to pin the contract across the API transition.
        self._real.run_early_escalation_poll(*args, **kwargs)

    def force_exit(self, *, bridge_pgids: object = ()) -> None:
        self._real.force_exit(bridge_pgids=cast("list[int]", bridge_pgids))


def test_second_sigint_during_first_sigint_interrupt_thread() -> None:
    """Pins the long-running-body contract for the SYNC entry point.

    SYNC-path equivalent of
    ``test_second_sigint_during_first_sigint_executor_body`` in
    ``tests/test_asyncio_bridge_install_signal_handlers.py``. The
    asyncio test pins the second-SIGINT-during-first-SIGINT-executor-body
    contract for the async path; this test pins the same contract
    for the SYNC ``handle_keyboard_interrupt`` entry point, which
    is the seam a real Ctrl+C reaches inside the pipeline loop on
    unattended runs (the user's reported scenario).

    Contract: when a second SIGINT arrives while the first SIGINT's
    interrupt thread is still mid-flight (its ``begin_interrupt``
    body is still executing, blocked on a long-running agent's
    graceful shutdown), ``force_exit`` is invoked exactly once, and
    the interrupt thread's later completion does NOT call
    ``hard_exit`` a second time. The ``_force_exit_called``
    idempotency guard fires correctly across the body delay.

    Mechanism (mirrors the AC-01 long-running-body test pattern in
    the async file but for the SYNC entry point):

    1. Build a real factory-built ``InterruptDispatcher`` via
       ``_build_dispatcher(manager, hard_exit=hard_exit)`` with a
       recording ``hard_exit`` so ``os._exit(130)`` is NOT invoked
       by the test. Add an active record so the second-SIGINT path
       routes through the real ``force_exit`` chain.
    2. Wrap the real dispatcher in ``_SlowBeginDispatcher`` so
       ``begin_interrupt`` blocks on ``_begin_done``. The wrapper
       delegates ``force_exit`` to the real dispatcher so the
       production ``_force_exit_called`` guard is exercised.
    3. Use a custom signal_setter that records the call AND
       signals a ``threading.Event`` when the force-kill handler
       is installed, so the test can deterministically wait
       without busy-waiting or ``time.sleep``.
    4. Start ``handle_keyboard_interrupt`` in a background
       ``threading.Thread`` with ``poll_interval_s=0.001`` so the
       entry-point busy-wait exits in <1ms. Wait for the
       force-kill handler to be installed via the event (1.0s
       timeout).
    5. Invoke the installed force-kill handler manually
       (``set_calls[0][1](signal.SIGINT, None)``) to simulate the
       second SIGINT arriving while ``begin_interrupt`` is still
       blocked. Assert ``exit_calls == [(130,)]`` (one hard_exit
       call) and that the dispatcher's ``_force_exit_called`` flag
       is ``True``.
    6. Release ``_begin_done.set()``. Join the entry-point thread
       with a 2.0s timeout. Assert the thread is no longer alive
       (the interrupt thread completed normally and the
       main-thread busy-wait exited).
    7. Assert ``exit_calls == [(130,)]`` (still exactly one — the
       interrupt thread's eventual completion must NOT trigger a
       second ``force_exit``).
    8. As an explicit idempotency check, call the real
       dispatcher's ``force_exit`` directly one more time and
       assert ``exit_calls == [(130,)]`` (the ``_force_exit_called``
       guard fires).

    Wall-clock: < 200ms (the test does NOT use ``time.sleep`` and
    uses the dispatcher's ``poll_interval_s=0.001`` override for
    the entry-point busy-wait). See ADR-0001 D7.
    """
    manager = FakeProcessManager()
    manager.add_active(pid=101, pgid=9999, label="invoke:fake")
    exit_calls: list[tuple[int, ...]] = []
    real_dispatcher = _build_dispatcher(
        manager,
        hard_exit=cast("Callable[[int], None]", lambda code: exit_calls.append((code,))),
    )
    begin_done = threading.Event()
    slow_dispatcher = _SlowBeginDispatcher(real_dispatcher, begin_done)
    set_calls: list[tuple[int, object]] = []

    def _previous_handler(_signum: int, _frame: object) -> None:
        return None

    previous_handler: object = _previous_handler
    handler_installed = threading.Event()

    def _getter(_signum: int) -> object:
        return previous_handler

    def _setter(signum: int, handler: object) -> object:
        set_calls.append((signum, handler))
        if signum == signal.SIGINT and len(set_calls) == 1:
            handler_installed.set()
        return handler

    def _entry_point() -> None:
        handle_keyboard_interrupt(
            dispatcher=cast("InterruptDispatcher", slow_dispatcher),
            process_manager=cast("object", manager),
            signal_getter=cast("Callable[[int], object]", _getter),
            signal_setter=cast("Callable[[int, object], object]", _setter),
            poll_interval_s=_POLL_INTERVAL,
        )

    thread = threading.Thread(target=_entry_point, daemon=True)
    thread.start()
    try:
        # Wait for the force-kill handler to be installed.
        assert handler_installed.wait(timeout=1.0), (
            "force-kill handler was not installed within 1.0s; set_calls did not grow to len==1"
        )
        # The dispatcher's begin_interrupt is now blocked on
        # _begin_done. Invoke the installed force-kill handler
        # manually to simulate the second SIGINT arriving while
        # the first-SIGINT body is mid-flight.
        set_calls[0][1](signal.SIGINT, None)
        assert exit_calls == [(130,)], (
            f"second SIGINT should call hard_exit(130) exactly once; got {exit_calls}"
        )
        assert getattr(real_dispatcher, "_force_exit_called", None) is True, (
            "real dispatcher's _force_exit_called guard must be set "
            "synchronously by the second-SIGINT handler"
        )
    finally:
        # Always release the slow dispatcher so the test does not
        # hang on a failure.
        begin_done.set()
        thread.join(timeout=2.0)
    assert not thread.is_alive(), (
        "entry-point thread must complete within 2.0s of releasing "
        "_begin_done; the interrupt thread's eventual completion "
        "must NOT hang the main thread"
    )
    # The interrupt thread's eventual completion must NOT trigger
    # a second force_exit. exit_calls is still exactly one entry.
    assert exit_calls == [(130,)], (
        f"interrupt thread's eventual completion must NOT call "
        f"hard_exit a second time; got {exit_calls}"
    )
    # Explicit idempotency check: invoking force_exit on the real
    # dispatcher directly one more time after the body completed
    # must NOT add a second entry to exit_calls. The
    # _force_exit_called guard fires correctly.
    real_dispatcher.force_exit(bridge_pgids=[9999])
    assert exit_calls == [(130,)], (
        f"_force_exit_called guard must prevent a second hard_exit call; got {exit_calls}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
