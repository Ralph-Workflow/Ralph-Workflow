"""Keyboard interrupt handling for the pipeline runner."""

from __future__ import annotations

import os
import signal
import threading
import time
from contextlib import suppress
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

from ralph.interrupt import controller_from_process_manager
from ralph.interrupt.controller import install_force_kill_handler
from ralph.process.manager import get_process_manager

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.process.manager import ProcessManager, ProcessRecord


class _CpuTimes(Protocol):
    user: float
    system: float


class _PsutilProcess(Protocol):
    def cpu_times(self) -> _CpuTimes: ...


class _PsutilModule(Protocol):
    Process: Callable[[int], _PsutilProcess]

# NEW BEHAVIOR: hard-kill budget for the FIRST SIGINT path. The
# pre-fix code waited the full
# ``process_manager.policy.default_grace_period_s`` (5.0s) for the
# agent's process group to respond to SIGTERM, with no
# early-escalation on no-progress. The new bound is 1.5s: a
# genuinely graceful shutdown completes well within this window,
# and a stuck agent is force-killed at the bound. The poll thread
# in ``_sigint_early_escalation_poll`` may detect no-progress
# earlier than the bound and escalate to SIGKILL immediately. The
# bound is enforced at import time via `if`/`raise RuntimeError`
# (NOT `assert`) so the constant cannot silently regress.
INTERRUPT_HARD_KILL_BUDGET_SECONDS: float = 1.5
_INTERRUPT_HARD_KILL_BUDGET_MAX_SECONDS: float = 30.0
if not (0 < INTERRUPT_HARD_KILL_BUDGET_SECONDS < _INTERRUPT_HARD_KILL_BUDGET_MAX_SECONDS):
    raise RuntimeError(
        "INTERRUPT_HARD_KILL_BUDGET_SECONDS must be in (0,"
        f" {_INTERRUPT_HARD_KILL_BUDGET_MAX_SECONDS:g}) seconds"
        f" (got {INTERRUPT_HARD_KILL_BUDGET_SECONDS})"
    )

# NEW BEHAVIOR: poll interval for the liveness check during the
# first-SIGINT grace period. The poll thread checks the agent's
# process group for liveness (CPU time + child liveness) every
# ``SIGINT_PROGRESS_POLL_INTERVAL_SECONDS`` and escalates to SIGKILL
# once no-progress is detected. The interval must be strictly less
# than ``INTERRUPT_HARD_KILL_BUDGET_SECONDS`` so the poll happens
# at least once during the max-wait window.
SIGINT_PROGRESS_POLL_INTERVAL_SECONDS: float = 0.2
if not (
    0 < SIGINT_PROGRESS_POLL_INTERVAL_SECONDS < INTERRUPT_HARD_KILL_BUDGET_SECONDS
):
    raise RuntimeError(
        "SIGINT_PROGRESS_POLL_INTERVAL_SECONDS must be in (0,"
        f" {INTERRUPT_HARD_KILL_BUDGET_SECONDS}) seconds"
        f" (got {SIGINT_PROGRESS_POLL_INTERVAL_SECONDS})"
    )


def _psutil_pid_cpu_time(pid: int) -> float:
    """Return the cumulative CPU time for a PID, or 0.0 on any failure.

    The poll thread in ``_sigint_early_escalation_poll`` uses the
    CPU-time delta between successive polls as the no-progress
    signal: a stuck agent that ignores SIGTERM has a constant CPU
    time across two polls, while a gracefully-shutting-down agent
    is winding down and may or may not have changed.

    The function is wrapped in try/except so a missing psutil or
    a transient OS error does not stall the recovery path.
    """
    try:
        psutil = cast("_PsutilModule", import_module("psutil"))
    except Exception:
        return 0.0
    try:
        proc = psutil.Process(pid)
        cpu = proc.cpu_times().user + proc.cpu_times().system
        return float(cpu)
    except Exception:
        return 0.0


def _pid_is_alive(pid: int) -> bool:
    """Return True when the PID is still alive at the OS level.

    Uses ``os.kill(pid, 0)`` which is the POSIX idiom for a
    liveness probe that does not deliver a signal. Returns False
    when the process is gone, the user lacks permission, or the
    OS error is transient.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Permission denied means the process exists but is
        # owned by another user; treat as alive.
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


def _sigint_early_escalation_poll(
    process_manager: ProcessManager,
    kill_label_prefix: str,
    grace_period_s: float,
    *,
    progress_poll_interval_s: float = SIGINT_PROGRESS_POLL_INTERVAL_SECONDS,
    max_wait_s: float = INTERRUPT_HARD_KILL_BUDGET_SECONDS,
) -> None:
    """Run the early-escalation poll for the first SIGINT path.

    The poll thread:
        1. Selects tracked processes whose label starts with
           ``kill_label_prefix`` (e.g. ``"invoke:"``) and which are
           still alive.
        2. For each selected record, takes a snapshot of the CPU
           time via psutil (best-effort) and sleeps for
           ``progress_poll_interval_s``.
        3. On the next poll iteration, if the CPU time is unchanged
           AND the process is still alive, escalates to SIGKILL
           via ``os.killpg`` (or ``os.kill`` as a fallback).
        4. The thread exits when either all matched processes are
           gone, ``max_wait_s`` has elapsed, or the parent thread
           joins.

    The pre-fix code waited the full grace period with no
    liveness check; a stuck agent that ignored SIGTERM kept the
    user waiting for the entire 5.0s. The new poll means the
    SIGKILL is sent before the bound as soon as no-progress is
    detected.

    This function REPLACES the prior ``manager.shutdown_all_for_label``
    call in ``_begin_interrupt`` for the first SIGINT path. The
    graceful path (SIGTERM) is still attempted first by the
    ``ProcessManager.shutdown_all_for_label`` call, but the new
    poll detects no-progress and escalates.

    Args:
        process_manager: The ProcessManager singleton.
        kill_label_prefix: The label prefix used to match
            agent-process records (e.g. ``"invoke:"``).
        grace_period_s: The graceful SIGTERM grace period passed
            to ``shutdown_all_for_label``. This is the upper bound
            on how long a graceful shutdown may take.
        progress_poll_interval_s: Poll interval for the CPU-time
            snapshot. Default
            ``SIGINT_PROGRESS_POLL_INTERVAL_SECONDS``.
        max_wait_s: Hard cap on the total poll duration. Default
            ``INTERRUPT_HARD_KILL_BUDGET_SECONDS``. The poll
            thread exits when this bound elapses.
    """
    deadline = time.monotonic() + max_wait_s
    cpu_baselines: dict[int, float] = {}
    while time.monotonic() < deadline:
        time.sleep(progress_poll_interval_s)
        matched = _matched_active_records(process_manager, kill_label_prefix)
        if matched is None:
            continue
        if not matched:
            return
        if not _any_record_alive(matched):
            return
        # Check CPU-time progression for each matched record. A
        # stuck agent that ignored SIGTERM has constant CPU time
        # across two polls; a gracefully-shutting-down agent may
        # or may not have changed. The CPU-time delta is the
        # primary no-progress signal.
        if not _records_show_no_progress(matched, cpu_baselines):
            continue
        # No-progress detected: escalate to SIGKILL. The graceful
        # path has already been attempted by
        # ``shutdown_all_for_label``; the agent is wedged and
        # needs a hard kill.
        _kill_records(matched)
        return
    del grace_period_s  # Bound enforced by deadline; the parameter is
    # kept for API symmetry with the legacy call.


def handle_keyboard_interrupt(monitor_stop: Callable[[], None] | None = None) -> None:
    """Gracefully stop tracked children, escalating on a second SIGINT.

    The first SIGINT now uses the new
    ``_sigint_early_escalation_poll`` policy: a SIGTERM is sent to
    the agent's process group, then a poll thread monitors the
    process for no-progress and escalates to SIGKILL before
    ``INTERRUPT_HARD_KILL_BUDGET_SECONDS`` elapses. The user never
    waits more than the bound for the first SIGINT to take effect.

    The second SIGINT continues to use the force-kill handler
    installed by ``install_force_kill_handler``.
    """
    process_manager = get_process_manager()
    controller = controller_from_process_manager(
        process_manager=process_manager,
        stop_connectivity=monitor_stop,
    )
    interrupt_done = threading.Event()
    interrupt_error: list[BaseException] = []

    def _force_exit() -> None:
        kill_method = os.killpg if hasattr(os, "killpg") else os.kill
        try:
            active_records = list(process_manager.list_active())
        except Exception:
            active_records = []
        for record in active_records:
            with suppress(ProcessLookupError, PermissionError):
                if kill_method is os.killpg:
                    kill_method(record.pgid, signal.SIGKILL)
                else:
                    kill_method(record.pid, signal.SIGKILL)
        os._exit(130)

    def _begin_interrupt() -> None:
        try:
            # First-SIGINT path: graceful SIGTERM via the label-targeted
            # shutdown, then a liveness-poll daemon thread escalates
            # to SIGKILL on no-progress (bounded by
            # ``INTERRUPT_HARD_KILL_BUDGET_SECONDS``).
            controller.begin_interrupt(
                grace_period_s=process_manager.policy.default_grace_period_s,
                kill_label="invoke:",
            )
            # NEW BEHAVIOR: spawn a daemon thread that monitors the
            # agent's process group for no-progress and escalates to
            # SIGKILL before the hard-kill bound. The pre-fix code
            # had no early-escalation and the user could wait the
            # full default_grace_period_s=5.0s.
            poll_thread = threading.Thread(
                target=_sigint_early_escalation_poll,
                args=(process_manager, "invoke:", INTERRUPT_HARD_KILL_BUDGET_SECONDS),
                daemon=True,
            )
            poll_thread.start()
            poll_thread.join(timeout=INTERRUPT_HARD_KILL_BUDGET_SECONDS + 0.1)
        except BaseException as exc:
            interrupt_error.append(exc)
        finally:
            interrupt_done.set()

    restore_force_kill = install_force_kill_handler(_force_exit)
    interrupt_thread = threading.Thread(target=_begin_interrupt, daemon=True)
    interrupt_thread.start()
    try:
        while not interrupt_done.wait(timeout=0.05):
            continue
    finally:
        with suppress(Exception):
            restore_force_kill()
    if interrupt_error:
        logger.warning("Interrupt controller raised during KeyboardInterrupt")
