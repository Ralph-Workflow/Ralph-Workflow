"""ManagedProcess — synchronous process wrapper with lifecycle integration."""

from __future__ import annotations

import contextlib
import io
import subprocess
import threading
import time as _time
from typing import IO, TYPE_CHECKING, Literal

from loguru import logger

from ralph.process.manager._deadline_timeout_expired import DeadlineTimeoutExpired
from ralph.process.manager._managed_process_output_limit_exceeded_error import (
    ManagedProcessOutputLimitExceededError,
)
from ralph.process.manager._process_status import _TERMINAL_STATUSES
from ralph.timeout_defaults import PROCESS_EXIT_WAIT_SECONDS

if TYPE_CHECKING:
    from _thread import LockType
    from collections.abc import Callable

    from ralph.process.manager._process_manager import ProcessManager
    from ralph.process.manager._process_manager_types import (
        _PsutilModuleLike,
        _PsutilProcessLike,
        _SyncProcessLike,
    )
    from ralph.process.manager._process_record import ProcessRecord


class ManagedProcess:
    """Wraps a synchronous process handle and integrates with ProcessManager lifecycle tracking."""

    def __init__(
        self,
        proc: _SyncProcessLike,
        record: ProcessRecord,
        manager: ProcessManager,
    ) -> None:
        self._proc = proc
        self._record = record
        self._manager = manager

    @property
    def record(self) -> ProcessRecord:
        return self._record

    @property
    def pid(self) -> int:
        return self._proc.pid

    @property
    def stdin(self) -> IO[bytes] | None:
        return self._proc.stdin

    @property
    def stdout(self) -> IO[bytes] | None:
        return self._proc.stdout

    @property
    def stderr(self) -> IO[bytes] | None:
        return self._proc.stderr

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode

    def termination_issuer(self) -> str | None:
        """Return the lifecycle issuer that intentionally stopped this process."""
        return self._manager.termination_issuer(self._record.pid)

    def record_terminal_reason(self, reason: str) -> None:
        """Authorize this process's termination with a named terminal reason."""
        self._manager.record_terminal_reason(self._record.pid, reason)

    def poll(self) -> int | None:
        rc = self._proc.poll()
        if rc is not None and self._record.status not in _TERMINAL_STATUSES:
            self._manager._mark_exited(self._record, rc)
        return rc

    def wait(self, timeout: float | None = None) -> int:
        try:
            rc = self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise
        if self._record.status not in _TERMINAL_STATUSES:
            self._manager._mark_exited(self._record, rc)
        return rc

    def communicate(
        self,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes | None, bytes | None]:
        try:
            stdout, stderr = self._proc.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise
        rc = self._proc.returncode
        if rc is None:
            rc = self._proc.wait(timeout=PROCESS_EXIT_WAIT_SECONDS)
        if rc is not None and self._record.status not in _TERMINAL_STATUSES:
            self._manager._mark_exited(self._record, rc)
        return stdout, stderr

    def _start_descendant_monitor(
        self,
        stop_event: threading.Event,
        observed_descendants: dict[int, _PsutilProcessLike],
        observed_lock: LockType,
    ) -> threading.Thread:
        def _observe_descendants() -> None:
            while not stop_event.wait(0.05):
                for proc in self._snapshot_live_descendants():
                    raw_pid: object = getattr(proc, "pid", None)
                    if isinstance(raw_pid, int):
                        with observed_lock:
                            observed_descendants[raw_pid] = proc

        thread = threading.Thread(target=_observe_descendants, daemon=True)
        thread.start()
        return thread

    def _observed_descendants(
        self,
        observed_descendants: dict[int, _PsutilProcessLike],
        observed_lock: LockType,
    ) -> list[_PsutilProcessLike]:
        with observed_lock:
            return list(observed_descendants.values())

    def _collect_live_descendants(
        self,
        psutil_mod: _PsutilModuleLike,
        groups: list[list[_PsutilProcessLike]],
    ) -> list[_PsutilProcessLike]:
        live_descendants: list[_PsutilProcessLike] = []
        seen_pids: set[int] = set()
        for group in groups:
            for proc in group:
                if not self._is_live_psutil_process(psutil_mod, proc):
                    continue
                raw_pid: object = getattr(proc, "pid", None)
                proc_pid = int(raw_pid) if isinstance(raw_pid, int) else id(proc)
                if proc_pid in seen_pids:
                    continue
                seen_pids.add(proc_pid)
                live_descendants.append(proc)
        return live_descendants

    def communicate_and_cleanup(
        self,
        input: bytes | None = None,
        timeout: float | None = None,
        cleanup_grace_period_s: float = 0.0,
        output_limit_bytes: int | None = None,
        on_output_chunk: Callable[[bytes], None] | None = None,
        *,
        hard_timeout: float | None = None,
    ) -> tuple[bytes | None, bytes | None]:
        """Drain output and clean up any descendant processes with psutil.

        ``timeout`` is an inactivity window when output capture is enabled:
        each stdout or stderr chunk resets it. ``hard_timeout`` is an optional
        absolute ceiling for that mode; callers that omit it keep legacy
        timeout-only behavior.

        Exactly ONE synchronous process-tree scan runs per call, and it
        runs *after* the child has finished. ``psutil``'s
        ``children(recursive=True)`` is O(every process on the machine)
        -- on macOS it walks the whole pid table building a ppid map --
        so a scan costs more wall-clock than a short-lived ``git``
        subprocess itself, and it gets *more* expensive exactly when
        several Ralph agents run side by side and the machine's process
        count climbs. The pre-``communicate`` scan this method used to
        take was pure cost with no coverage: it ran microseconds after
        our own ``spawn``, before the child could have forked anything,
        so it could only ever observe the empty tree the post-run scan
        observes again. Descendants that appear *while* the child runs
        are picked up by the background monitor thread below, and
        descendants still alive at exit are picked up by the trailing
        scan; both are preserved.
        """
        psutil_mod = self._manager._psutil
        observed_descendants: dict[int, _PsutilProcessLike] = {}
        observed_lock = threading.Lock()
        stop_monitor = threading.Event()
        monitor_thread = (
            self._start_descendant_monitor(stop_monitor, observed_descendants, observed_lock)
            if psutil_mod is not None
            else None
        )

        try:
            if output_limit_bytes is None:
                stdout, stderr = self.communicate(input=input, timeout=timeout)
            else:
                stdout, stderr = self._communicate_with_output_limit(
                    input=input,
                    timeout=timeout,
                    output_limit_bytes=output_limit_bytes,
                    cleanup_grace_period_s=cleanup_grace_period_s,
                    on_output_chunk=on_output_chunk,
                    hard_timeout=hard_timeout,
                )
        except (subprocess.TimeoutExpired, ManagedProcessOutputLimitExceededError):
            with contextlib.suppress(Exception):
                self.terminate(grace_period_s=cleanup_grace_period_s)
            if psutil_mod is not None:
                live_descendants = self._collect_live_descendants(
                    psutil_mod,
                    [
                        self._observed_descendants(observed_descendants, observed_lock),
                        self._snapshot_live_descendants(),
                    ],
                )
                if live_descendants:
                    with contextlib.suppress(Exception):
                        self._cleanup_descendant_waves(
                            psutil_mod, live_descendants, cleanup_grace_period_s
                        )
            raise
        finally:
            stop_monitor.set()
            if monitor_thread is not None:
                with contextlib.suppress(Exception):
                    monitor_thread.join(timeout=0.5)

        if psutil_mod is not None:
            live_descendants = self._collect_live_descendants(
                psutil_mod,
                [
                    self._observed_descendants(observed_descendants, observed_lock),
                    self._snapshot_live_descendants(),
                ],
            )
            if live_descendants:
                self._cleanup_descendant_waves(psutil_mod, live_descendants, cleanup_grace_period_s)
        return stdout, stderr

    def _write_input_and_close_stdin(self, input: bytes | None) -> None:
        if input is None or self.stdin is None:
            return
        with contextlib.suppress(Exception):
            self.stdin.write(input)
            self.stdin.flush()
        with contextlib.suppress(Exception):
            self.stdin.close()

    def _append_output_tail(self, buffer: bytearray, chunk: bytes, output_limit_bytes: int) -> None:
        if output_limit_bytes <= 0:
            return
        buffer.extend(chunk)
        overflow = len(buffer) - output_limit_bytes
        if overflow > 0:
            del buffer[:overflow]

    def _close_output_pipes(self) -> None:
        for pipe in (self.stdout, self.stderr):
            if pipe is not None:
                with contextlib.suppress(Exception):
                    pipe.close()

    def _read_output_stream(
        self,
        stream: IO[bytes] | None,
        buffer: bytearray,
        output_limit_bytes: int,
        limit_exceeded: threading.Event,
        output_lock: threading.Lock,
        total_output_bytes_ref: list[int],
        last_output_at_ref: list[float],
        on_chunk: Callable[[bytes], None] | None = None,
    ) -> None:
        if stream is None:
            return
        while True:
            chunk = stream.read1(8_192) if isinstance(stream, io.BufferedIOBase) else stream.read(8_192)
            if not chunk:
                break
            if on_chunk is not None:
                with contextlib.suppress(Exception):
                    on_chunk(chunk)
            with output_lock:
                total_output_bytes_ref[0] += len(chunk)
                last_output_at_ref[0] = _time.monotonic()
                self._append_output_tail(buffer, chunk, output_limit_bytes)
                if total_output_bytes_ref[0] > output_limit_bytes:
                    limit_exceeded.set()

    def _communicate_with_output_limit(
        self,
        *,
        input: bytes | None,
        timeout: float | None,
        output_limit_bytes: int,
        cleanup_grace_period_s: float,
        on_output_chunk: Callable[[bytes], None] | None = None,
        hard_timeout: float | None = None,
    ) -> tuple[bytes, bytes]:
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        total_output_bytes_ref = [0]
        start_at = _time.monotonic()
        last_output_at_ref = [start_at]
        output_lock = threading.Lock()
        limit_exceeded = threading.Event()

        self._write_input_and_close_stdin(input)

        stdout_thread = threading.Thread(
            target=self._read_output_stream,
            args=(
                self.stdout,
                stdout_buffer,
                output_limit_bytes,
                limit_exceeded,
                output_lock,
                total_output_bytes_ref,
                last_output_at_ref,
                on_output_chunk,
            ),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=self._read_output_stream,
            args=(
                self.stderr,
                stderr_buffer,
                output_limit_bytes,
                limit_exceeded,
                output_lock,
                total_output_bytes_ref,
                last_output_at_ref,
                on_output_chunk,
            ),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        # ``hard_timeout`` prevents a continuously chatty child from running forever.
        absolute_deadline = start_at + hard_timeout if hard_timeout is not None else None
        try:
            while stdout_thread.is_alive() or stderr_thread.is_alive():
                if limit_exceeded.is_set():
                    with contextlib.suppress(Exception):
                        self.terminate(grace_period_s=cleanup_grace_period_s)
                    break
                now = _time.monotonic()
                with output_lock:
                    inactivity_deadline = (
                        last_output_at_ref[0] + timeout if timeout is not None else None
                    )
                    timeout_stdout = bytes(stdout_buffer)
                    timeout_stderr = bytes(stderr_buffer)
                timeout_cause: Literal["inactivity", "hard_cap"] | None = None
                timeout_value: float | None = None
                if absolute_deadline is not None and now >= absolute_deadline:
                    timeout_cause = "hard_cap"
                    timeout_value = hard_timeout
                elif inactivity_deadline is not None and now >= inactivity_deadline:
                    timeout_cause = "inactivity"
                    timeout_value = timeout
                if timeout_cause is not None:
                    if timeout_value is None:
                        raise RuntimeError("timeout deadline requires a timeout value")
                    # Close the pipes before the finally block joins their readers.
                    self.terminate(grace_period_s=cleanup_grace_period_s)
                    raise DeadlineTimeoutExpired(
                        [],
                        timeout_value,
                        timeout_cause=timeout_cause,
                        output=timeout_stdout,
                        stderr=timeout_stderr,
                    )
                stdout_thread.join(timeout=0.05)
                stderr_thread.join(timeout=0.05)
        finally:
            stdout_thread.join(timeout=0.5)
            stderr_thread.join(timeout=0.5)
            self._close_output_pipes()

        rc = self.wait(timeout=0.5)
        stdout = bytes(stdout_buffer)
        stderr = bytes(stderr_buffer)
        if limit_exceeded.is_set():
            raise ManagedProcessOutputLimitExceededError(
                output_limit_bytes=output_limit_bytes,
                stdout=stdout,
                stderr=stderr,
            )
        if self._record.status not in _TERMINAL_STATUSES:
            self._manager._mark_exited(self._record, rc)
        return stdout, stderr

    def _snapshot_live_descendants(self) -> list[_PsutilProcessLike]:
        psutil_mod = self._manager._psutil
        if psutil_mod is None:
            return []
        try:
            root = psutil_mod.process_from_pid(self.pid)
            descendants = root.children(recursive=True)
        except (
            psutil_mod.NoSuchProcess,
            psutil_mod.AccessDenied,
            PermissionError,
            OSError,
        ):
            return []
        return [proc for proc in descendants if self._is_live_psutil_process(psutil_mod, proc)]

    def _is_live_psutil_process(
        self, psutil_mod: _PsutilModuleLike, proc: _PsutilProcessLike
    ) -> bool:
        try:
            return proc.is_running() and proc.status() != "zombie"
        except (
            psutil_mod.NoSuchProcess,
            psutil_mod.AccessDenied,
            PermissionError,
            OSError,
        ):
            return False

    def _collect_live_direct_children(
        self, psutil_mod: _PsutilModuleLike, processes: list[_PsutilProcessLike]
    ) -> list[_PsutilProcessLike]:
        live_children: list[_PsutilProcessLike] = []
        seen_pids: set[int] = set()
        for proc in processes:
            try:
                children = proc.children(recursive=False)
            except (
                psutil_mod.NoSuchProcess,
                psutil_mod.AccessDenied,
                PermissionError,
                OSError,
            ):
                continue
            for child in children:
                if not self._is_live_psutil_process(psutil_mod, child):
                    continue
                raw_pid: object = getattr(child, "pid", None)
                child_pid = int(raw_pid) if isinstance(raw_pid, int) else id(child)
                if child_pid in seen_pids:
                    continue
                seen_pids.add(child_pid)
                live_children.append(child)
        return live_children

    def _terminate_psutil_wave(
        self,
        psutil_mod: _PsutilModuleLike,
        procs: list[_PsutilProcessLike],
        grace_period_s: float,
    ) -> list[_PsutilProcessLike]:
        if not procs:
            return []
        for proc in procs:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                proc.terminate()
        # _safe_wait_procs: psutil's wait_procs can raise OSError (EINVAL) from
        # os.pidfd_open on an already-reaped PID; the helper classifies per-PID.
        _, alive = self._manager._safe_wait_procs(psutil_mod, procs, timeout=grace_period_s)
        return [proc for proc in alive if self._is_live_psutil_process(psutil_mod, proc)]

    def _kill_psutil_wave(
        self,
        psutil_mod: _PsutilModuleLike,
        procs: list[_PsutilProcessLike],
    ) -> list[_PsutilProcessLike]:
        if not procs:
            return []
        for proc in procs:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                proc.kill()
        # _safe_wait_procs: psutil's wait_procs can raise OSError (EINVAL) from
        # os.pidfd_open on an already-reaped PID; the helper classifies per-PID.
        _, still_alive = self._manager._safe_wait_procs(
            psutil_mod, procs, timeout=self._manager.policy.kill_followup_timeout_s
        )
        return [proc for proc in still_alive if self._is_live_psutil_process(psutil_mod, proc)]

    def _cleanup_descendant_waves(
        self,
        psutil_mod: _PsutilModuleLike,
        snapshot_descendants: list[_PsutilProcessLike],
        cleanup_grace_period_s: float,
    ) -> None:
        snapshot_survivors = self._terminate_psutil_wave(
            psutil_mod, snapshot_descendants, cleanup_grace_period_s
        )
        if snapshot_survivors:
            self._kill_psutil_wave(psutil_mod, snapshot_survivors)

        first_late_spawns = self._collect_live_direct_children(psutil_mod, snapshot_descendants)
        if first_late_spawns:
            self._kill_psutil_wave(psutil_mod, first_late_spawns)

        second_late_spawns = self._collect_live_direct_children(psutil_mod, first_late_spawns)
        if second_late_spawns:
            self._kill_psutil_wave(psutil_mod, second_late_spawns)

    def terminate(self, grace_period_s: float | None = None) -> None:
        # Idempotency guard: if already terminal, skip without error
        if self._record.status in _TERMINAL_STATUSES:
            logger.debug(
                f"Process {self._record.pid} already terminal ({self._record.status.name}), "
                f"skipping terminate"
            )
            return
        self._manager.record_terminal_reason(self._record.pid, "operator_cancellation", "managed_process.terminate")
        gp = (
            grace_period_s
            if grace_period_s is not None
            else self._manager.policy.default_grace_period_s
        )
        self._manager._escalate_termination_sync(self._record, self._proc, gp)

    def kill(self) -> None:
        self._manager.record_terminal_reason(self._record.pid, "operator_cancellation", "managed_process.terminate")
        self._manager._escalate_termination_sync(self._record, self._proc, 0.0)

    def cleanup_orphans(self) -> None:
        """Kill descendants that outlived this process."""
        self._manager.cleanup_orphans(self)

    def has_live_descendants(self) -> bool:
        """Return True when this process currently has live descendants."""
        psutil_mod = self._manager._psutil
        if psutil_mod is None:
            return False
        try:
            root = psutil_mod.process_from_pid(self.pid)
            descendants = root.children(recursive=True)
        except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
            return False

        for child in descendants:
            try:
                if child.is_running() and child.status() != "zombie":
                    return True
            except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                continue
        return False

    def descendant_snapshot(self) -> tuple[int, float | None]:
        """Return (live_descendant_count, oldest_age_seconds) excluding zombies."""
        psutil_mod = self._manager._psutil
        if psutil_mod is None:
            return (0, None)
        try:
            root = psutil_mod.process_from_pid(self.pid)
            descendants = root.children(recursive=True)
        except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
            return (0, None)

        now = _time.monotonic()
        count = 0
        oldest_age: float | None = None
        for child in descendants:
            try:
                if not (child.is_running() and child.status() != "zombie"):
                    continue
                count += 1
                try:
                    create_time = child.create_time()
                    age = now - create_time
                    if oldest_age is None or age > oldest_age:
                        oldest_age = age
                except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                    pass
            except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                continue
        return (count, oldest_age)

    def __enter__(self) -> ManagedProcess:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if self._record.status not in _TERMINAL_STATUSES:
            self.terminate(grace_period_s=2.0)
        for attr in ("stdout", "stderr", "stdin"):
            pipe: IO[bytes] | IO[str] | None = getattr(self._proc, attr, None)
            if pipe is not None:
                with contextlib.suppress(Exception):
                    pipe.close()
        if self._record.status not in _TERMINAL_STATUSES:
            # Bounded so teardown of a wedged child cannot hang; TimeoutExpired is
            # suppressed and the process tree was already terminated above.
            with contextlib.suppress(Exception):
                self._proc.wait(timeout=PROCESS_EXIT_WAIT_SECONDS)


__all__ = ["DeadlineTimeoutExpired", "ManagedProcess"]
