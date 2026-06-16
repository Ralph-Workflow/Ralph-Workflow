"""InterruptDispatcher — the single seam that wires interrupt handling.

This module is the canonical home for the constants and helper logic that
both the sync ``handle_keyboard_interrupt`` path (``ralph.pipeline._runner_interrupt``)
and the asyncio path (``ralph.interrupt.asyncio_bridge.install_signal_handlers``)
route through. The :class:`InterruptDispatcher` is the single seam that
binds an :class:`InterruptController` to a ``ProcessManager``, an optional
connectivity-stop callback, and a hard-exit function. Every future change
to the SIGINT wiring happens here; the legacy inline ``_force_exit``
helper in ``_runner_interrupt`` and the per-callsite kills in
``asyncio_bridge`` are now thin wrappers.

The dataclass is intentionally small: a controller, a process manager,
a hard-exit field, a poll interval, a hard-kill budget, a kill-label
default, and an internal ``_force_exit_called`` flag that gives the
dispatcher idempotency on ``force_exit`` (the controller has none).
The ``begin_interrupt`` method wraps the controller's begin_interrupt
to inject the dispatcher's ``kill_label`` and to optionally block until
the process manager's active-record list is empty (closing the
orphan-process gap when the CLI catches a ``KeyboardInterrupt``).
"""

from __future__ import annotations

import importlib
import os
import signal
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

from ralph.interrupt.controller import (
    INTERRUPT_EXIT_CODE,
    InterruptController,
    controller_from_process_manager,
    install_force_kill_handler,
)
from ralph.interrupt.state import request_user_interrupt
from ralph.process.manager import get_process_manager

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ralph.interrupt.signal_getter import SignalGetter
    from ralph.interrupt.signal_setter import SignalSetter
    from ralph.process.manager import ProcessManager, ProcessRecord


# --- Module-level constants ------------------------------------------------
# These constants are the canonical home of the early-escalation timing
# policy. The sync ``handle_keyboard_interrupt`` re-exports them for
# backward compatibility. The dataclass uses them as defaults; tests
# can override the dataclass fields to run in < 1s.

INTERRUPT_HARD_KILL_BUDGET_SECONDS: float = 1.5
_INTERRUPT_HARD_KILL_BUDGET_MAX_SECONDS: float = 30.0
if not (0 < INTERRUPT_HARD_KILL_BUDGET_SECONDS < _INTERRUPT_HARD_KILL_BUDGET_MAX_SECONDS):
    raise RuntimeError(
        "INTERRUPT_HARD_KILL_BUDGET_SECONDS must be in (0,"
        f" {_INTERRUPT_HARD_KILL_BUDGET_MAX_SECONDS:g}) seconds"
        f" (got {INTERRUPT_HARD_KILL_BUDGET_SECONDS})"
    )

SIGINT_PROGRESS_POLL_INTERVAL_SECONDS: float = 0.2
if not (0 < SIGINT_PROGRESS_POLL_INTERVAL_SECONDS < INTERRUPT_HARD_KILL_BUDGET_SECONDS):
    raise RuntimeError(
        "SIGINT_PROGRESS_POLL_INTERVAL_SECONDS must be in (0,"
        f" {INTERRUPT_HARD_KILL_BUDGET_SECONDS}) seconds"
        f" (got {SIGINT_PROGRESS_POLL_INTERVAL_SECONDS})"
    )

# Tighter exact-value pin: the canonical values are 1.5 seconds
# for INTERRUPT_HARD_KILL_BUDGET_SECONDS and 0.2 seconds for
# SIGINT_PROGRESS_POLL_INTERVAL_SECONDS. The range checks above
# accept any value in the valid range; this exact pin ensures a
# future regression that picks a different in-range value is
# caught at import time (immune to ``python -O`` because the
# check uses ``if``/``raise`` not ``assert``).
_INTERRUPT_HARD_KILL_BUDGET_REQUIRED: float = 1.5
_FLOAT_EPSILON: float = 1e-9
if (
    not abs(INTERRUPT_HARD_KILL_BUDGET_SECONDS - _INTERRUPT_HARD_KILL_BUDGET_REQUIRED)
    < _FLOAT_EPSILON
):
    raise RuntimeError(
        f"INTERRUPT_HARD_KILL_BUDGET_SECONDS must be "
        f"{_INTERRUPT_HARD_KILL_BUDGET_REQUIRED} "
        f"(got {INTERRUPT_HARD_KILL_BUDGET_SECONDS})"
    )

_SIGINT_PROGRESS_POLL_INTERVAL_REQUIRED: float = 0.2
if (
    not abs(SIGINT_PROGRESS_POLL_INTERVAL_SECONDS - _SIGINT_PROGRESS_POLL_INTERVAL_REQUIRED)
    < _FLOAT_EPSILON
):
    raise RuntimeError(
        f"SIGINT_PROGRESS_POLL_INTERVAL_SECONDS must be "
        f"{_SIGINT_PROGRESS_POLL_INTERVAL_REQUIRED} "
        f"(got {SIGINT_PROGRESS_POLL_INTERVAL_SECONDS})"
    )


_DEFAULT_SIGNAL_GETTER = cast("SignalGetter", signal.getsignal)
_DEFAULT_SIGNAL_SETTER = cast("SignalSetter", signal.signal)


class _CpuTimes(Protocol):
    user: float
    system: float


class _PsutilProcess(Protocol):
    def cpu_times(self) -> _CpuTimes: ...


class _PsutilModule(Protocol):
    Process: Callable[[int], _PsutilProcess]


def _psutil_pid_cpu_time(pid: int) -> float:
    """Return the cumulative CPU time for a PID, or 0.0 on any failure."""
    try:
        psutil = cast("_PsutilModule", importlib.import_module("psutil"))
    except Exception:
        return 0.0
    try:
        proc = psutil.Process(pid)
        cpu = proc.cpu_times().user + proc.cpu_times().system
        return float(cpu)
    except Exception:
        return 0.0


def _pid_is_alive(pid: int) -> bool:
    """Return True when the PID is still alive at the OS level."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _matched_active_records(
    process_manager: ProcessManager, kill_label_prefix: str
) -> list[ProcessRecord] | None:
    try:
        active = list(process_manager.list_active())
    except Exception:
        return None
    return [
        record
        for record in active
        if record.label is not None and record.label.startswith(kill_label_prefix)
    ]


def _any_record_alive(records: list[ProcessRecord]) -> bool:
    return any(_pid_is_alive(record.pid) for record in records)


def _records_show_no_progress(
    records: list[ProcessRecord],
    cpu_baselines: dict[int, float],
) -> bool:
    no_progress = True
    for record in records:
        pid = record.pid
        current_cpu = _psutil_pid_cpu_time(pid)
        previous_cpu = cpu_baselines.get(pid)
        if previous_cpu is None:
            cpu_baselines[pid] = current_cpu
            no_progress = False
            continue
        if current_cpu != previous_cpu:
            no_progress = False
            break
    return no_progress


def _kill_records(records: list[ProcessRecord]) -> None:
    kill_method = os.killpg if hasattr(os, "killpg") else os.kill
    for record in records:
        with suppress(ProcessLookupError, PermissionError):
            if kill_method is os.killpg:
                kill_method(record.pgid, signal.SIGKILL)
            else:
                kill_method(record.pid, signal.SIGKILL)


def _dispatch_kill(process_manager: ProcessManager, records: list[ProcessRecord]) -> None:
    """Send SIGKILL to each record's PGID, preferring the process
    manager's ``kill_process_group`` seam when available so the test
    can record the kill through the fake. Falls back to ``os.killpg``
    or ``os.kill`` (no PGID available) for the real manager.
    """
    kill_method: Callable[[int, int], None] | None = getattr(
        process_manager, "kill_process_group", None
    )
    if callable(kill_method):
        for record in records:
            with suppress(ProcessLookupError, PermissionError):
                kill_method(record.pgid, signal.SIGKILL)
        return
    _kill_records(records)


@dataclass(frozen=True)
class InterruptDispatcher:
    """Single seam for SIGINT handling.

    Wires an :class:`InterruptController` to a ``ProcessManager``, a
    connectivity-stop callback, and a hard-exit function. ``begin_interrupt``
    forwards to the controller with the dispatcher's ``kill_label``
    default (``'invoke:'``) and optionally blocks until the process
    manager's active-record list is empty. ``force_exit`` is idempotent
    — repeated calls are no-ops — closing the double-invocation gap
    that the raw controller has.
    """

    controller: InterruptController
    process_manager: ProcessManager
    hard_exit: Callable[[int], None] | None
    poll_interval_s: float
    hard_kill_budget_s: float
    kill_label: str = "invoke:"
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _force_exit_called: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.hard_kill_budget_s <= 0:
            raise RuntimeError(
                f"hard_kill_budget_s must be positive (got {self.hard_kill_budget_s})"
            )
        if self.poll_interval_s <= 0:
            raise RuntimeError(f"poll_interval_s must be positive (got {self.poll_interval_s})")
        if not isinstance(self.clock(), float):
            raise RuntimeError(f"clock() must return a float (got {type(self.clock()).__name__})")

    def begin_interrupt(
        self,
        grace_period_s: float | None = None,
        *,
        block: bool = False,
    ) -> None:
        """Record the interrupt, route to the controller with ``kill_label``."""
        if grace_period_s is None:
            grace_period_s = self.process_manager.policy.default_grace_period_s
        self.controller.begin_interrupt(
            grace_period_s=grace_period_s,
            kill_label=self.kill_label,
        )
        if block:
            self._wait_for_list_active_empty(grace_period_s=grace_period_s)

    def _wait_for_list_active_empty(self, grace_period_s: float) -> None:
        """Block until ``process_manager.list_active()`` is empty or the
        grace period elapses. The grace period is the upper bound on the
        wait; a process that exits faster resolves the wait early.

        If the grace deadline elapses with active records still present,
        the dispatcher escalates via ``self.force_exit(bridge_pids=...)``
        to break the frozen-pipeline-after-Ctrl+C failure mode. The
        escalation is idempotent: a subsequent force_exit (e.g. from
        a second SIGINT) is a no-op.
        """
        deadline = self.clock() + grace_period_s
        while self.clock() < deadline:
            try:
                if not self.process_manager.list_active():
                    return
            except Exception:
                return
            remaining = max(deadline - self.clock(), 0.0)
            self.sleep(min(self.poll_interval_s, remaining))
        # Deadline elapsed with records still active: escalate to force_exit.
        try:
            active = self.process_manager.list_active()
        except Exception:
            return
        if active:
            self.force_exit(bridge_pgids=[r.pgid for r in active])

    def force_exit(
        self,
        bridge_pgids: Iterable[int] = (),
        **kwargs: object,
    ) -> None:
        """Escalate to immediate tracked-process termination and exit.

        Idempotent: repeated calls are no-ops. The first call sets the
        internal ``_force_exit_called`` flag (via ``object.__setattr__``,
        since the dataclass is frozen), routes through the controller's
        ``force_interrupt`` for tracked-process shutdown, and then
        invokes the exit callable. The dispatcher's own ``hard_exit``
        field is preferred; if it is None, the controller's
        ``force_exit`` is invoked so the controller's injected exit
        callable is the one that runs (PA-019 thread-through).

        The ``bridge_pids`` keyword is accepted for backward
        compatibility; it is deprecated and emits a single loguru
        warning when used. New callers MUST pass ``bridge_pgids``.
        """
        bridge_pids_legacy = cast("Iterable[int]", kwargs.pop("bridge_pids", ()))
        if bridge_pids_legacy:
            logger.warning("bridge_pids is deprecated; pass bridge_pgids instead")
        pgids: Iterable[int] = list(bridge_pgids) if bridge_pgids else list(bridge_pids_legacy)
        if self._force_exit_called:
            return
        object.__setattr__(self, "_force_exit_called", True)
        self.controller.force_interrupt(bridge_pgids=pgids)
        if self.hard_exit is not None:
            self.hard_exit(INTERRUPT_EXIT_CODE)
        else:
            self.controller.force_exit(bridge_pgids=pgids)

    def run_early_escalation_poll(
        self,
        *,
        progress_poll_interval_s: float | None = None,
        max_wait_s: float | None = None,
    ) -> None:
        """Public utility: run the CPU-progress early-escalation poll.

        This method is a public utility kept for backward compatibility
        but is NOT used by the production seam. The production seam in
        ``run_shutdown_block`` uses ``begin_interrupt(block=True)``
        which routes through the dispatcher's liveness-based
        ``_wait_for_list_active_empty`` (waiting for
        ``process_manager.list_active()`` to drain or the grace
        deadline to elapse). The liveness-based path does NOT use
        CPU-progress detection; an alive-but-zero-CPU long-running
        agent (writing a checkpoint, releasing a lock, draining a
        queue) is given the full ``grace_period_s`` to die naturally
        before the dispatcher escalates via ``force_exit``.

        This CPU-progress-based method is retained for callers that
        need it. The method polls the matched active records (whose
        label starts with the dispatcher's ``kill_label``) and
        SIGKILLs them on no-progress. Bounded by ``max_wait_s``
        (defaults to ``self.hard_kill_budget_s``). Mirrors the prior
        inline helper in
        ``_runner_interrupt._sigint_early_escalation_poll``. The
        method's dedicated tests in
        ``tests/test_interrupt_dispatcher.py``
        (``test_early_escalation_poll_kills_when_no_cpu_progress_within_budget``,
        ``test_early_escalation_poll_does_not_kill_when_cpu_progresses``,
        ``test_early_escalation_poll_exits_when_process_dies``)
        still pass against the public method.
        """
        poll = (
            progress_poll_interval_s
            if progress_poll_interval_s is not None
            else self.poll_interval_s
        )
        bound = max_wait_s if max_wait_s is not None else self.hard_kill_budget_s
        deadline = self.clock() + bound
        cpu_baselines: dict[int, float] = {}
        while self.clock() < deadline:
            self.sleep(poll)
            matched = _matched_active_records(self.process_manager, self.kill_label)
            if matched is None:
                continue
            if not matched:
                return
            if not _any_record_alive(matched):
                return
            if not _records_show_no_progress(matched, cpu_baselines):
                continue
            _dispatch_kill(self.process_manager, matched)
            return


def dispatcher_from_process_manager(
    *,
    process_manager: ProcessManager | None = None,
    stop_connectivity: Callable[[], None] | None = None,
    record_interrupt: Callable[[], None] | None = None,
    kill_process_group: Callable[[int, int], None] | None = None,
    hard_exit: Callable[[int], None] | None = None,
    poll_interval_s: float = SIGINT_PROGRESS_POLL_INTERVAL_SECONDS,
    hard_kill_budget_s: float = INTERRUPT_HARD_KILL_BUDGET_SECONDS,
    kill_label: str = "invoke:",
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> InterruptDispatcher:
    """Build an :class:`InterruptDispatcher` from a ProcessManager instance.

    Threads ``hard_exit`` and ``kill_process_group`` into the controller
    factory so the controller's own force_exit path uses the same
    injected exit callable. The ``hard_exit`` is also stored on the
    dispatcher (the dispatcher's force_exit invokes the dispatcher's
    own field, not the controller's). The ``clock`` and ``sleep``
    kwargs default to ``time.monotonic`` and ``time.sleep`` and are
    forwarded to the dispatcher so tests can inject fakes.
    """
    resolved_record_interrupt = (
        record_interrupt if record_interrupt is not None else request_user_interrupt
    )
    controller = controller_from_process_manager(
        process_manager=process_manager,
        stop_connectivity=stop_connectivity,
        record_interrupt=resolved_record_interrupt,
        kill_process_group=kill_process_group,
        hard_exit=hard_exit,
    )
    resolved_pm = process_manager if process_manager is not None else get_process_manager()
    return InterruptDispatcher(
        controller=controller,
        process_manager=resolved_pm,
        hard_exit=hard_exit,
        poll_interval_s=poll_interval_s,
        hard_kill_budget_s=hard_kill_budget_s,
        kill_label=kill_label,
        clock=clock,
        sleep=sleep,
    )


def handle_keyboard_interrupt_at_cli(
    *,
    process_manager: ProcessManager | None = None,
    record_interrupt: Callable[[], None] | None = None,
    poll_interval_s: float = SIGINT_PROGRESS_POLL_INTERVAL_SECONDS,
    hard_kill_budget_s: float = INTERRUPT_HARD_KILL_BUDGET_SECONDS,
    kill_label: str = "invoke:",
    exit_code: int = INTERRUPT_EXIT_CODE,
) -> int:
    """Canonical CLI-level entry point for handling ``KeyboardInterrupt``.

    Consolidates the near-duplicate inline catches in
    ``ralph.cli.main._run_pipeline`` and ``ralph.cli.commands.run.run``
    behind a single helper. The helper:

    1. Builds an :class:`InterruptDispatcher` via the factory.
    2. Calls ``begin_interrupt(grace_period_s=..., block=True)`` so the
       agent's process group is SIGTERMed via
       ``shutdown_all_for_label('invoke:', grace)`` and the CLI
       catch blocks until the process manager's active list drains
       (or escalates via ``force_exit`` on deadline expiration).
    3. Returns ``exit_code`` (default ``INTERRUPT_EXIT_CODE = 130``).

    Strategy A: this helper does NOT wrap the dispatcher call in
    ``try/except``. It propagates any exception. The two CLI catches
    each wrap the helper call in their own ``try/except`` and emit
    the verbatim "Interrupt dispatcher failed during outer CLI catch" /
    "during CLI catch" log warning. This preserves bit-for-bit
    production output and lets the canonical block=True contract be
    black-box tested in isolation.
    """
    dispatcher = dispatcher_from_process_manager(
        process_manager=process_manager,
        record_interrupt=record_interrupt,
        poll_interval_s=poll_interval_s,
        hard_kill_budget_s=hard_kill_budget_s,
        kill_label=kill_label,
    )
    if process_manager is not None:
        grace_period_s = process_manager.policy.default_grace_period_s
    else:
        grace_period_s = get_process_manager().policy.default_grace_period_s
    dispatcher.begin_interrupt(grace_period_s=grace_period_s, block=True)
    return exit_code


def run_shutdown_block(
    dispatcher: InterruptDispatcher,
    *,
    grace_period_s: float,
    join_timeout_s: float = INTERRUPT_HARD_KILL_BUDGET_SECONDS + 0.1,
    error_log_message: str = "Interrupt shutdown block raised",
) -> None:
    """Canonical seam for the first-SIGINT shutdown block.

    Both the SYNC ``handle_keyboard_interrupt`` entry point
    (``ralph.pipeline._runner_interrupt._begin_interrupt``) and the
    asyncio ``install_signal_handlers`` entry point
    (``ralph.interrupt.asyncio_bridge._shutdown_block``) route
    through this helper so the bodies cannot drift. The 7th
    architectural seam is ``error_log_message``: the SYNC path
    passes ``"Interrupt controller raised during KeyboardInterrupt"``
    (preserved for bit-for-bit production log output) and the
    asyncio path passes the existing
    ``"Interrupt shutdown block raised"`` (preserved for the
    same reason).

    The body is a single call to
    ``dispatcher.begin_interrupt(grace_period_s=grace_period_s,
    block=True)`` only — no daemon thread, no ``threading.Thread.join``.
    The dispatcher uses its liveness-based
    ``_wait_for_list_active_empty`` (via ``block=True``) to wait for
    the process manager's active-record list to drain, escalating
    via ``force_exit`` only when the grace deadline elapses with
    records still active. This replaces the prior CPU-progress-based
    ``run_early_escalation_poll`` daemon thread, which SIGKILLed
    alive-but-zero-CPU long-running agents (writing checkpoints,
    releasing locks, draining queues) prematurely. The
    ``run_early_escalation_poll`` method is kept on the dispatcher
    as a public utility NOT used by the production seam (see its
    docstring); the method's dedicated tests still pass against the
    public method.

    The ``join_timeout_s`` parameter is now unused and is kept for
    backward compatibility with the prior call shape. The two call
    sites (``ralph/pipeline/_runner_interrupt.py`` and
    ``ralph/interrupt/asyncio_bridge.py``) do not pass the kwarg
    and the helper is byte-for-byte equivalent at those sites.

    The helper is added to ``__all__`` so ``from
    ralph.interrupt.dispatcher import *`` exposes it. See
    ADR-0001 D7 and D8.
    """
    # ``join_timeout_s`` is no longer used by the production seam:
    # the dispatcher's begin_interrupt(block=True) waits via the
    # liveness-based _wait_for_list_active_empty path, which polls
    # list_active() on the dispatcher's clock/sleep seams. The
    # parameter is kept (defaulted) for backward compatibility so
    # existing callers do not have to change.
    del join_timeout_s
    try:
        dispatcher.begin_interrupt(grace_period_s=grace_period_s, block=True)
    except Exception:
        logger.warning(error_log_message)


__all__ = [
    "INTERRUPT_HARD_KILL_BUDGET_SECONDS",
    "SIGINT_PROGRESS_POLL_INTERVAL_SECONDS",
    "InterruptDispatcher",
    "dispatcher_from_process_manager",
    "handle_keyboard_interrupt_at_cli",
    "install_force_kill_handler",
    "run_shutdown_block",
]
