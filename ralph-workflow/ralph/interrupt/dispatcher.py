
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


_DEFAULT_SIGNAL_GETTER = cast(
    "SignalGetter", signal.getsignal
)
_DEFAULT_SIGNAL_SETTER = cast(
    "SignalSetter", signal.signal
)


class _CpuTimes(Protocol):
    user: float
    system: float


class _PsutilProcess(Protocol):
    def cpu_times(self) -> _CpuTimes: ...


class _PsutilModule(Protocol):
    Process: Callable[[int], _PsutilProcess]


def _psutil_pid_cpu_time(pid: int) -> float:
    try:
        psutil = cast(
            "_PsutilModule", importlib.import_module("psutil")
        )
    except Exception:
        return 0.0
    try:
        proc = psutil.Process(pid)
        cpu = proc.cpu_times().user + proc.cpu_times().system
        return float(cpu)
    except Exception:
        return 0.0


def _pid_is_alive(pid: int) -> bool:
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


def _signalable_pgid(record: ProcessRecord) -> int | None:
    pgid = record.pgid
    return pgid if pgid > 1 else None


def _kill_records(records: list[ProcessRecord]) -> None:
    use_killpg = hasattr(os, "killpg")
    for record in records:
        with suppress(ProcessLookupError, PermissionError):
            if use_killpg:
                pgid = _signalable_pgid(record)
                if pgid is not None:
                    os.killpg(pgid, signal.SIGKILL)
            elif record.pid > 1:
                os.kill(record.pid, signal.SIGKILL)


def _dispatch_kill(process_manager: ProcessManager, records: list[ProcessRecord]) -> None:
    kill_method: Callable[[int, int], None] | None = getattr(
        process_manager, "kill_process_group", None
    )
    if callable(kill_method):
        for record in records:
            pgid = _signalable_pgid(record)
            if pgid is None:
                continue
            with suppress(ProcessLookupError, PermissionError):
                kill_method(pgid, signal.SIGKILL)
        return
    _kill_records(records)


@dataclass(frozen=True)
class InterruptDispatcher:

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
        if grace_period_s is None:
            grace_period_s = self.process_manager.policy.default_grace_period_s
        self.controller.begin_interrupt(
            grace_period_s=grace_period_s,
            kill_label=self.kill_label,
        )
        if block:
            self._wait_for_list_active_empty(grace_period_s=grace_period_s)

    def _wait_for_list_active_empty(self, grace_period_s: float) -> None:
        deadline = self.clock() + grace_period_s
        while self.clock() < deadline:
            try:
                if not self.process_manager.list_active():
                    return
            except Exception:
                return
            remaining = max(deadline - self.clock(), 0.0)
            self.sleep(min(self.poll_interval_s, remaining))
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
        bridge_pids_legacy = cast(
            "Iterable[int]", kwargs.pop("bridge_pids", ())
        )
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
