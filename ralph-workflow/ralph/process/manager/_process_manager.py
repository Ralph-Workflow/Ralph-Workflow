"""ProcessManager — single source of truth for every child process Ralph spawns."""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import threading
from collections import OrderedDict
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from loguru import logger

from ralph.process.manager._managed_async_process import ManagedAsyncProcess
from ralph.process.manager._managed_process import ManagedProcess
from ralph.process.manager._managed_pty_process import ManagedPtyProcess
from ralph.process.manager._process_event import ProcessEvent
from ralph.process.manager._process_liveness import LivenessResult, verify_process_liveness
from ralph.process.manager._process_manager_policy import ProcessManagerPolicy
from ralph.process.manager._process_manager_runtime import loguru_event_listener
from ralph.process.manager._process_manager_types import (
    _async_cell,
    _AsyncProcessFactory,
    _AsyncProcessLike,
    _PsutilModuleLike,
    _pty_cell,
    _PtyProcessFactory,
    _PtyProcessLike,
    _sync_cell,
    _SyncProcessFactory,
    _SyncProcessLike,
)
from ralph.process.manager._process_record import ProcessRecord
from ralph.process.manager._process_status import _TERMINAL_STATUSES, ProcessStatus
from ralph.process.manager._process_termination_error import ProcessTerminationError
from ralph.process.manager._pty_spawn_options import PtySpawnOptions
from ralph.process.manager._spawn_options import SpawnOptions

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


class ProcessManager:
    """Single source of truth for all child processes Ralph spawns.

    Termination uses psutil for cross-platform process-tree teardown: graceful
    terminate() to the root and all descendants, then forceful kill() to
    survivors. Works identically on Linux, macOS, and Windows.

    Use :func:`get_process_manager` to obtain the module-level singleton.
    Inject a custom instance (with a test-friendly :class:`ProcessManagerPolicy`)
    to keep tests fast and isolated.

    For testing, inject custom process factories and psutil module via the
    constructor to avoid spawning real processes::

        from ralph.testing.fake_process import (
            FakePopen, FakeAsyncProcess, FakePsutil,
            make_sync_process_factory, make_async_process_factory,
        )
        import itertools

        pm = ProcessManager(
            policy=_FAST_POLICY,
            sync_process_factory=make_sync_process_factory(itertools.count(1)),
            async_process_factory=make_async_process_factory(itertools.count(1)),
            psutil=FakePsutil(),
        )
    """

    def __init__(
        self,
        policy: ProcessManagerPolicy | None = None,
        sync_process_factory: _SyncProcessFactory | None = None,
        async_process_factory: _AsyncProcessFactory | None = None,
        pty_process_factory: _PtyProcessFactory | None = None,
        psutil: _PsutilModuleLike | None = None,
    ) -> None:
        self.policy = policy or ProcessManagerPolicy()
        self._records: dict[int, ProcessRecord] = {}
        self._terminal_records: OrderedDict[int, ProcessRecord] = OrderedDict()
        self._sync_procs: dict[int, _SyncProcessLike] = {}
        self._pty_procs: dict[int, _PtyProcessLike] = {}
        self._descendants: dict[int, list[int]] = {}
        self._termination_outcomes: dict[int, list[dict[str, str]]] = {}

        # purge_on_init: clear terminal records on startup when policy requests it
        if self.policy.purge_on_init:
            self._terminal_records.clear()

        # Concurrent shutdown TOCTOU audit:
        # (A) shutdown_all() status filter - SAFE without lock: concurrent callers may both
        #     dispatch termination; ProcessLookupError/NoSuchProcess suppressed at every step
        # (B) _escalate_termination_sync() terminal precheck - SAFE without lock: same suppression
        # (C) _mark_exited/_mark_killed status write - PROTECTED by this lock: writing
        #     ProcessRecord.status, returncode, ended_at must be atomic; duplicate writes
        #     corrupt fields and fire duplicate events. _emit called OUTSIDE the lock to
        #     prevent deadlock from listeners that call back into ProcessManager.
        self._status_lock = threading.Lock()

        self._async_procs: dict[int, _AsyncProcessLike] = {}
        self._listeners: dict[int, Callable[[ProcessEvent], None]] = {}
        self._listener_counter = 0
        sf = (
            sync_process_factory
            if sync_process_factory is not None
            else next(iter(_sync_cell), None)
        )
        af = (
            async_process_factory
            if async_process_factory is not None
            else next(iter(_async_cell), None)
        )
        pf = pty_process_factory if pty_process_factory is not None else next(iter(_pty_cell), None)
        assert sf is not None and af is not None and pf is not None, (
            "No process factories set; import ralph.process.manager before creating ProcessManager"
        )
        self._sync_process_factory: _SyncProcessFactory = sf
        self._async_process_factory: _AsyncProcessFactory = af
        self._pty_process_factory: _PtyProcessFactory = pf
        self._psutil = psutil
        if self.policy.log_events:
            self.register_listener(loguru_event_listener)

    def register_listener(self, callback: Callable[[ProcessEvent], None]) -> Callable[[], None]:
        """Subscribe to lifecycle events.  Returns an unsubscribe callable."""
        lid = self._listener_counter
        self._listener_counter += 1
        self._listeners[lid] = callback

        def unsubscribe() -> None:
            self._listeners.pop(lid, None)

        return unsubscribe

    # ------------------------------------------------------------------
    # Descendant registry API
    # ------------------------------------------------------------------

    def register_descendant(self, parent_pid: int, descendant_pid: int) -> None:
        """Register a descendant PID under a tracked parent.

        When the parent process is terminated, descendants registered
        here are enumerated and terminated as well.

        Args:
            parent_pid: The PID of the parent process.
            descendant_pid: The PID of the descendant to register.
        """
        if parent_pid not in self._descendants:
            self._descendants[parent_pid] = []
        if descendant_pid not in self._descendants[parent_pid]:
            self._descendants[parent_pid].append(descendant_pid)

    def _record_termination_outcome(self, pid: int, stage: str, outcome: str) -> None:
        """Record a termination outcome for a PID."""
        if pid not in self._termination_outcomes:
            self._termination_outcomes[pid] = []
        self._termination_outcomes[pid].append({"stage": stage, "outcome": outcome})

    def list_termination_outcomes(self) -> dict[int, list[dict[str, str]]]:
        """Return a dict mapping PID to termination outcome records.

        Each outcome dict has 'stage' and 'outcome' keys.
        Returns empty dict when no outcomes have been recorded.

        Returns:
            Dict[int, list[dict[str, str]]]: PID-keyed list of outcome dicts.
        """
        return dict(self._termination_outcomes)

    # ------------------------------------------------------------------
    # Spawn methods
    # ------------------------------------------------------------------

    def spawn(
        self,
        command: Sequence[str],
        opts: SpawnOptions | None = None,
    ) -> ManagedProcess:
        """Spawn a synchronous child process and begin tracking it."""
        effective = opts or SpawnOptions()
        cmd = tuple(command)
        now = datetime.now(tz=UTC)
        try:
            proc: _SyncProcessLike = self._sync_process_factory(cmd, effective)
        except OSError as exc:
            record = ProcessRecord(
                pid=-1,
                pgid=-1,
                command=cmd,
                cwd=effective.cwd,
                started_at=now,
                status=ProcessStatus.FAILED,
                ended_at=datetime.now(tz=UTC),
                cause="failed",
                failure_message=str(exc),
                label=effective.label,
            )
            self._emit(record, ProcessStatus.SPAWNED, ProcessStatus.FAILED)
            raise

        pid = proc.pid
        try:
            pgid = os.getpgid(pid) if hasattr(os, "getpgid") else pid
        except (ProcessLookupError, OSError):
            pgid = pid

        record = ProcessRecord(
            pid=pid,
            pgid=pgid,
            command=cmd,
            cwd=effective.cwd,
            started_at=now,
            status=ProcessStatus.RUNNING,
            label=effective.label,
        )
        self._terminal_records.pop(pid, None)
        self._records[pid] = record
        self._sync_procs[pid] = proc
        self._emit(record, ProcessStatus.SPAWNED, ProcessStatus.RUNNING)
        return ManagedProcess(proc, record, self)

    def spawn_pty(
        self,
        command: Sequence[str],
        opts: PtySpawnOptions | None = None,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        cols: int = 80,
        rows: int = 24,
        label: str | None = None,
    ) -> ManagedPtyProcess:
        """Spawn a PTY-backed child process and begin tracking it."""
        effective = opts or PtySpawnOptions(cwd=cwd, env=env, cols=cols, rows=rows, label=label)
        cmd = tuple(command)
        now = datetime.now(tz=UTC)
        try:
            proc = self._pty_process_factory(
                cmd,
                cwd=effective.cwd,
                env=effective.env,
                cols=effective.cols,
                rows=effective.rows,
            )
        except OSError as exc:
            record = ProcessRecord(
                pid=-1,
                pgid=-1,
                command=cmd,
                cwd=effective.cwd,
                started_at=now,
                status=ProcessStatus.FAILED,
                ended_at=datetime.now(tz=UTC),
                cause="failed",
                failure_message=str(exc),
                label=effective.label,
            )
            self._emit(record, ProcessStatus.SPAWNED, ProcessStatus.FAILED)
            raise

        pid = proc.pid
        try:
            pgid = os.getpgid(pid) if hasattr(os, "getpgid") else pid
        except (ProcessLookupError, OSError):
            pgid = pid

        record = ProcessRecord(
            pid=pid,
            pgid=pgid,
            command=cmd,
            cwd=effective.cwd,
            started_at=now,
            status=ProcessStatus.RUNNING,
            label=effective.label,
        )
        self._terminal_records.pop(pid, None)
        self._records[pid] = record
        self._pty_procs[pid] = proc
        self._emit(record, ProcessStatus.SPAWNED, ProcessStatus.RUNNING)
        return ManagedPtyProcess(proc, record, self)

    async def spawn_async(
        self,
        command: Sequence[str],
        opts: SpawnOptions | None = None,
    ) -> ManagedAsyncProcess:
        """Spawn an async child process and begin tracking it."""
        effective = opts or SpawnOptions()
        cmd = tuple(command)
        now = datetime.now(tz=UTC)
        try:
            proc = await self._async_process_factory(
                cmd,
                cwd=effective.cwd,
                env=effective.env,
                stdin=effective.stdin,
                stdout=effective.stdout,
                stderr=effective.stderr,
                start_new_session=effective.start_new_session,
            )
        except OSError as exc:
            record = ProcessRecord(
                pid=-1,
                pgid=-1,
                command=cmd,
                cwd=effective.cwd,
                started_at=now,
                status=ProcessStatus.FAILED,
                ended_at=datetime.now(tz=UTC),
                cause="failed",
                failure_message=str(exc),
                label=effective.label,
            )
            self._emit(record, ProcessStatus.SPAWNED, ProcessStatus.FAILED)
            raise

        pid = proc.pid
        try:
            pgid = os.getpgid(pid) if hasattr(os, "getpgid") else pid
        except (ProcessLookupError, OSError):
            pgid = pid

        record = ProcessRecord(
            pid=pid,
            pgid=pgid,
            command=cmd,
            cwd=effective.cwd,
            started_at=now,
            status=ProcessStatus.RUNNING,
            label=effective.label,
        )
        self._terminal_records.pop(pid, None)
        self._records[pid] = record
        self._async_procs[pid] = proc
        self._emit(record, ProcessStatus.SPAWNED, ProcessStatus.RUNNING)
        return ManagedAsyncProcess(proc, record, self)

    def list_active(self) -> list[ProcessRecord]:
        """Return all records for processes that have not yet terminated."""
        return [
            r
            for r in list(self._records.values())
            if r.status in (ProcessStatus.SPAWNED, ProcessStatus.RUNNING)
        ]

    def get_record(self, pid: int, *, include_terminal: bool = True) -> ProcessRecord | None:
        """Return a tracked record by pid, preferring active records over terminal history."""
        record = self._records.get(pid)
        if record is not None:
            return record
        if not include_terminal:
            return None
        return self._terminal_records.get(pid)

    def list_records(
        self,
        *,
        include_active: bool = True,
        include_terminal: bool = False,
        label_prefix: str | None = None,
    ) -> list[ProcessRecord]:
        """Return active and/or terminal records with optional label filtering."""
        records: list[ProcessRecord] = []
        if include_active:
            records.extend(self._records.values())
        if include_terminal:
            records.extend(self._terminal_records.values())
        if label_prefix is None:
            return list(records)
        return [
            record
            for record in records
            if record.label is not None and record.label.startswith(label_prefix)
        ]

    def terminate(
        self,
        handle: ManagedProcess | ManagedPtyProcess | ManagedAsyncProcess,
        *,
        grace_period_s: float | None = None,
    ) -> None:
        """Terminate a tracked process with escalation."""
        gp = grace_period_s if grace_period_s is not None else self.policy.default_grace_period_s
        if isinstance(handle, ManagedProcess):
            self._escalate_termination_sync(handle.record, handle._proc, gp)
            return
        if isinstance(handle, ManagedPtyProcess):
            self._escalate_termination_pty(handle.record, handle._proc, gp)

    # ------------------------------------------------------------------
    # Shutdown methods (with stale-entry reconciliation)
    # ------------------------------------------------------------------

    def _reconcile_stale_entries(self) -> int:
        """Scan active records and mark as KILLED any PIDs no longer alive at the OS level.

        Also cleans up _descendants entries for stale PIDs.

        Returns:
            Number of stale PIDs that were reconciled.
        """
        reconciled = 0
        for pid in list(self._records.keys()):
            record = self._records.get(pid)
            if record is None or record.status in _TERMINAL_STATUSES:
                continue
            liveness = verify_process_liveness(pid, psutil_mod=self._psutil)
            if liveness in (LivenessResult.GONE, LivenessResult.UNKNOWN):
                self._mark_killed(record, returncode=None, cause="stale_entry_reconciled")
                logger.debug(f"Stale tracking entry reconciled: PID {pid} no longer exists")
                reconciled += 1
            elif liveness == LivenessResult.ZOMBIE:
                self._mark_killed(record, returncode=None, cause="zombie_reconciled")
                logger.warning(f"Stale zombie entry reconciled: PID {pid} is zombie")
                reconciled += 1
        return reconciled

    def shutdown_all(self, *, grace_period_s: float | None = None) -> None:
        """Terminate all active processes."""
        gp = grace_period_s if grace_period_s is not None else self.policy.default_grace_period_s

        # Reconcile stale entries before attempting termination
        self._reconcile_stale_entries()

        for pid, record in list(self._records.items()):
            if record.status not in _TERMINAL_STATUSES:
                proc = self._sync_procs.get(pid)
                if proc is not None:
                    self._escalate_termination_sync(record, proc, gp)
                    continue
                pty_proc = self._pty_procs.get(pid)
                if pty_proc is not None:
                    self._escalate_termination_pty(record, pty_proc, gp)
                    continue
                async_proc = self._async_procs.get(pid)
                if async_proc is not None:
                    self._escalate_async_in_sync_context(record, async_proc, gp)
                    continue
                self._terminate_by_pid(record, gp)

    def shutdown_all_for_label(
        self, label_prefix: str, *, grace_period_s: float | None = None
    ) -> None:
        """Terminate all active processes whose label starts with label_prefix."""
        gp = grace_period_s if grace_period_s is not None else self.policy.default_grace_period_s

        # Reconcile stale entries before attempting termination
        self._reconcile_stale_entries()

        for pid, record in list(self._records.items()):
            if (
                record.label is not None
                and record.label.startswith(label_prefix)
                and record.status not in _TERMINAL_STATUSES
            ):
                proc = self._sync_procs.get(pid)
                if proc is not None:
                    self._escalate_termination_sync(record, proc, gp)
                    continue
                pty_proc = self._pty_procs.get(pid)
                if pty_proc is not None:
                    self._escalate_termination_pty(record, pty_proc, gp)
                    continue
                async_proc = self._async_procs.get(pid)
                if async_proc is not None:
                    self._escalate_async_in_sync_context(record, async_proc, gp)
                    continue
                self._terminate_by_pid(record, gp)

    # ------------------------------------------------------------------
    # Termination methods (with pre-kill liveness checks)
    # ------------------------------------------------------------------

    def _terminate_root_only_sync(
        self,
        record: ProcessRecord,
        proc: _SyncProcessLike | _PtyProcessLike,
        grace_period_s: float,
    ) -> None:
        # Pre-kill liveness check
        liveness = verify_process_liveness(record.pid, psutil_mod=self._psutil)
        if liveness == LivenessResult.GONE:
            self._mark_killed(record, None, cause="already_gone")
            logger.debug(f"Process {record.pid} already gone before terminate — marked killed")
            return

        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        logger.debug(
            f"Process {record.pid} graceful terminate sent, waiting {grace_period_s}s"
        )
        try:
            rc = proc.wait(timeout=grace_period_s)
        except (subprocess.TimeoutExpired, TimeoutError):
            logger.warning(
                f"Process {record.pid} survived graceful terminate, escalating to force kill"
            )
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            try:
                rc = proc.wait(timeout=self.policy.kill_followup_timeout_s)
            except (subprocess.TimeoutExpired, TimeoutError):
                post_liveness = verify_process_liveness(record.pid, psutil_mod=self._psutil)
                if post_liveness == LivenessResult.ZOMBIE:
                    logger.warning(
                        f"Process {record.pid} is zombie after force kill — parent must reap"
                    )
                    self._mark_killed(record, proc.poll(), cause="zombie_after_kill")
                    return
                if post_liveness != LivenessResult.ALIVE:
                    self._mark_killed(record, proc.poll(), cause="killed")
                    return
                self._mark_termination_failed(record, proc.poll())
                logger.error("Process {} still alive after kill", record.pid)
                raise ProcessTerminationError(
                    record.pid, record.pgid,
                    stage="force_kill",
                    reason="still alive",
                ) from None
        self._mark_killed(record, rc)

    async def _terminate_root_only_async(
        self,
        record: ProcessRecord,
        proc: _AsyncProcessLike,
        grace_period_s: float,
    ) -> None:
        # Pre-kill liveness check
        liveness = verify_process_liveness(record.pid, psutil_mod=self._psutil)
        if liveness == LivenessResult.GONE:
            self._mark_killed(record, proc.returncode, cause="already_gone")
            logger.debug(f"Process {record.pid} already gone before terminate — marked killed")
            return

        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        logger.debug(
            f"Process {record.pid} graceful terminate sent, waiting {grace_period_s}s"
        )
        try:
            rc = await asyncio.wait_for(
                proc.wait(),  # mcp-timeout-ok: wait_for-bounded
                timeout=grace_period_s,
            )
        except TimeoutError:
            logger.warning(
                f"Process {record.pid} survived graceful terminate, escalating to force kill"
            )
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            try:
                rc = await asyncio.wait_for(
                    proc.wait(),  # mcp-timeout-ok: wait_for-bounded
                    timeout=self.policy.kill_followup_timeout_s,
                )
            except TimeoutError:
                post_liveness = verify_process_liveness(record.pid, psutil_mod=self._psutil)
                if post_liveness == LivenessResult.ZOMBIE:
                    logger.warning(
                        f"Process {record.pid} is zombie after force kill — parent must reap"
                    )
                    self._mark_killed(record, proc.returncode, cause="zombie_after_kill")
                    return
                if post_liveness != LivenessResult.ALIVE:
                    self._mark_killed(record, proc.returncode, cause="killed")
                    return
                self._mark_termination_failed(record, proc.returncode)
                logger.error("Process {} still alive after kill", record.pid)
                raise ProcessTerminationError(
                    record.pid, record.pgid,
                    stage="force_kill",
                    reason="still alive",
                ) from None
        self._mark_killed(record, rc)

    def _terminate_by_pid(self, record: ProcessRecord, grace_period_s: float) -> None:
        psutil_mod = self._psutil
        if psutil_mod is None:
            return

        # Pre-kill liveness check
        liveness = verify_process_liveness(record.pid, psutil_mod=psutil_mod)
        if liveness == LivenessResult.GONE:
            self._mark_killed(record, cause="already_gone")
            logger.debug(f"Process {record.pid} already gone before terminate — marked killed")
            return

        try:
            root = psutil_mod.process_from_pid(record.pid)
            children = root.children(recursive=True)
        except psutil_mod.NoSuchProcess:
            self._mark_killed(record)
            return
        except psutil_mod.AccessDenied:
            self._raise_access_denied_termination(record)

        all_procs = [root, *children]
        for proc in all_procs:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                proc.terminate()
        logger.debug(
            f"Process {record.pid} graceful terminate sent to {len(all_procs)} procs, "
            f"waiting {grace_period_s}s"
        )

        _, alive = psutil_mod.wait_procs(all_procs, timeout=grace_period_s)
        if alive:
            logger.warning(
                f"Process {record.pid} survived graceful terminate, escalating to force kill"
            )
        for proc in alive:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                proc.kill()

        _, still_alive = psutil_mod.wait_procs(alive, timeout=self.policy.kill_followup_timeout_s)
        if still_alive:
            # Post-kill zombie/liveness check on survivors
            for p in still_alive:
                pid_liveness = verify_process_liveness(p.pid, psutil_mod=psutil_mod)
                if pid_liveness == LivenessResult.ZOMBIE:
                    logger.warning(
                        f"Process {p.pid} is zombie after force kill — parent must reap"
                    )
                    self._mark_killed(record, cause="zombie_after_kill")
                    return
            self._mark_termination_failed(record)
            logger.error("Process {} still alive after kill", record.pid)
            raise ProcessTerminationError(
                record.pid, record.pgid,
                stage="force_kill",
                reason="still alive",
            )
        self._mark_killed(record)

    def _escalate_termination_sync(
        self,
        record: ProcessRecord,
        proc: _SyncProcessLike | _PtyProcessLike,
        grace_period_s: float,
    ) -> None:
        if record.status in _TERMINAL_STATUSES:
            return

        # Pre-kill liveness check
        liveness = verify_process_liveness(record.pid, psutil_mod=self._psutil)
        if liveness == LivenessResult.GONE:
            self._mark_killed(record, proc.poll(), cause="already_gone")
            logger.debug(f"Process {record.pid} already gone before terminate — marked killed")
            return

        psutil_mod = self._psutil
        if psutil_mod is None:
            self._terminate_root_only_sync(record, proc, grace_period_s)
            return

        try:
            root = psutil_mod.process_from_pid(record.pid)
            children = root.children(recursive=True)
        except psutil_mod.NoSuchProcess:
            self._mark_killed(record, proc.poll())
            return
        except psutil_mod.AccessDenied:
            self._raise_access_denied_termination(record, proc.poll())

        all_procs = [root, *children]
        self._record_termination_outcome(record.pid, "graceful_terminate", "sent")
        for p in all_procs:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                p.terminate()
        logger.debug(
            f"Process {record.pid} graceful terminate sent to {len(all_procs)} procs, "
            f"waiting {grace_period_s}s"
        )

        _, alive = psutil_mod.wait_procs(all_procs, timeout=grace_period_s)
        if alive:
            logger.warning(
                f"Process {record.pid} survived graceful terminate, escalating to force kill"
            )
            self._record_termination_outcome(record.pid, "force_kill", "sent")
        for p in alive:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                p.kill()

        _, still_alive = psutil_mod.wait_procs(alive, timeout=self.policy.kill_followup_timeout_s)
        rc = proc.poll()
        if still_alive:
            # Post-kill zombie/liveness check on survivors
            for p in still_alive:
                pid_liveness = verify_process_liveness(p.pid, psutil_mod=psutil_mod)
                if pid_liveness == LivenessResult.ZOMBIE:
                    logger.warning(
                        f"Process {p.pid} is zombie after force kill — parent must reap"
                    )
                    self._mark_killed(record, rc, cause="zombie_after_kill")
                    return
            self._mark_termination_failed(record, rc)
            logger.error("Process {} still alive after kill", record.pid)
            raise ProcessTerminationError(
                record.pid, record.pgid,
                stage="force_kill",
                reason="still alive",
            )
        self._mark_killed(record, rc)

    def _escalate_termination_pty(
        self,
        record: ProcessRecord,
        proc: _PtyProcessLike,
        grace_period_s: float,
    ) -> None:
        if record.status in _TERMINAL_STATUSES:
            return
        # Pre-kill liveness check
        liveness = verify_process_liveness(record.pid, psutil_mod=self._psutil)
        if liveness == LivenessResult.GONE:
            self._mark_killed(record, proc.poll(), cause="already_gone")
            logger.debug(f"Process {record.pid} already gone before terminate — marked killed")
            return
        try:
            self._escalate_termination_sync(record, proc, grace_period_s)
        finally:
            with contextlib.suppress(Exception):
                proc.close()

    async def _escalate_termination_async(
        self,
        record: ProcessRecord,
        proc: _AsyncProcessLike,
        grace_period_s: float,
    ) -> None:
        if record.status in _TERMINAL_STATUSES:
            return

        # Pre-kill liveness check
        liveness = verify_process_liveness(record.pid, psutil_mod=self._psutil)
        if liveness == LivenessResult.GONE:
            self._mark_killed(record, proc.returncode, cause="already_gone")
            logger.debug(f"Process {record.pid} already gone before terminate — marked killed")
            return

        psutil_mod = self._psutil
        if psutil_mod is None:
            await self._terminate_root_only_async(record, proc, grace_period_s)
            return

        pid = record.pid
        policy_kill = self.policy.kill_followup_timeout_s

        def _do_terminate() -> bool:
            try:
                root = psutil_mod.process_from_pid(pid)
                children = root.children(recursive=True)
            except psutil_mod.NoSuchProcess:
                return False
            except psutil_mod.AccessDenied as exc:
                raise PermissionError from exc
            all_procs = [root, *children]
            for p in all_procs:
                with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                    p.terminate()
            _, alive = psutil_mod.wait_procs(all_procs, timeout=grace_period_s)
            for p in alive:
                with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                    p.kill()
            _, still_alive = psutil_mod.wait_procs(alive, timeout=policy_kill)
            return bool(still_alive)

        loop = asyncio.get_running_loop()
        try:
            still_alive = await loop.run_in_executor(None, _do_terminate)
        except PermissionError:
            self._raise_access_denied_termination(record, proc.returncode)
        rc = proc.returncode
        if still_alive:
            # Post-kill zombie/liveness check
            post_liveness = verify_process_liveness(record.pid, psutil_mod=psutil_mod)
            if post_liveness == LivenessResult.ZOMBIE:
                logger.warning(
                    f"Process {pid} is zombie after force kill — parent must reap"
                )
                self._mark_killed(record, rc, cause="zombie_after_kill")
                return
            self._mark_termination_failed(record, rc)
            logger.error("Process {} still alive after kill", pid)
            raise ProcessTerminationError(
                record.pid, record.pgid,
                stage="force_kill",
                reason="still alive",
            )
        self._mark_killed(record, rc)

    def _escalate_with_psutil(
        self,
        record: ProcessRecord,
        proc: _AsyncProcessLike,
        psutil_mod: _PsutilModuleLike,
        grace_period_s: float,
    ) -> None:
        try:
            root = psutil_mod.process_from_pid(record.pid)
            children = root.children(recursive=True)
        except psutil_mod.NoSuchProcess:
            self._mark_killed(record, proc.returncode)
            return
        except psutil_mod.AccessDenied:
            self._raise_access_denied_termination(record, proc.returncode)
        all_procs = [root, *children]
        for p in all_procs:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                p.terminate()
        logger.debug(
            f"Process {record.pid} graceful terminate sent to {len(all_procs)} procs, "
            f"waiting {grace_period_s}s"
        )
        _, alive = psutil_mod.wait_procs(all_procs, timeout=grace_period_s)
        if alive:
            logger.warning(
                f"Process {record.pid} survived graceful terminate, escalating to force kill"
            )
        for p in alive:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                p.kill()
        _, still_alive = psutil_mod.wait_procs(alive, timeout=self.policy.kill_followup_timeout_s)
        if still_alive:
            for p in still_alive:
                post_liveness = verify_process_liveness(p.pid, psutil_mod=psutil_mod)
                if post_liveness == LivenessResult.ZOMBIE:
                    logger.warning(f"Process {p.pid} is zombie after force kill — parent must reap")
                    self._mark_killed(record, proc.returncode, cause="zombie_after_kill")
                    return
            self._mark_termination_failed(record, proc.returncode)
            logger.error("Process {} still alive after kill", record.pid)
            raise ProcessTerminationError(
                record.pid, record.pgid, stage="force_kill", reason="still alive"
            )
        self._mark_killed(record, proc.returncode)

    def _escalate_without_psutil(self, record: ProcessRecord, proc: _AsyncProcessLike) -> None:
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.terminate()
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        probe_result = verify_process_liveness(record.pid, psutil_mod=None)
        if probe_result == LivenessResult.GONE:
            self._mark_killed(record, None)
            return
        if probe_result == LivenessResult.ZOMBIE:
            logger.warning(f"Process {record.pid} is zombie after force kill — parent must reap")
            self._mark_killed(record, None, cause="zombie_after_kill")
            return
        self._mark_termination_failed(record, None)
        raise ProcessTerminationError(
            record.pid, record.pgid, stage="force_kill", reason="still alive"
        )

    def _escalate_async_in_sync_context(
        self,
        record: ProcessRecord,
        proc: _AsyncProcessLike,
        grace_period_s: float,
    ) -> None:
        if record.status in _TERMINAL_STATUSES:
            return
        liveness = verify_process_liveness(record.pid, psutil_mod=self._psutil)
        if liveness == LivenessResult.GONE:
            self._mark_killed(record, proc.returncode, cause="already_gone")
            logger.debug(f"Process {record.pid} already gone before terminate — marked killed")
            return
        psutil_mod = self._psutil
        if psutil_mod is not None:
            self._escalate_with_psutil(record, proc, psutil_mod, grace_period_s)
        else:
            self._escalate_without_psutil(record, proc)

    def _raise_access_denied_termination(
        self, record: ProcessRecord, returncode: int | None = None
    ) -> None:
        self._mark_termination_failed(
            record,
            returncode,
            reason="Access denied while terminating process",
        )
        logger.error("Access denied while terminating process {}", record.pid)
        raise ProcessTerminationError(
            record.pid, record.pgid,
            stage="access_denied",
            reason="Access denied while terminating process",
        )

    def _record_terminal_state(self, record: ProcessRecord) -> None:
        self._records.pop(record.pid, None)
        limit = max(self.policy.terminal_history_limit, 0)
        if limit == 0:
            self._terminal_records.clear()
            return
        self._terminal_records.pop(record.pid, None)
        self._terminal_records[record.pid] = record
        while len(self._terminal_records) > limit:
            self._terminal_records.popitem(last=False)

    def _mark_exited(self, record: ProcessRecord, returncode: int | None) -> None:
        with self._status_lock:
            if record.status in _TERMINAL_STATUSES:
                return
            prev = record.status
            record.status = ProcessStatus.EXITED
            record.returncode = returncode
            record.ended_at = datetime.now(tz=UTC)
            record.cause = "exited"
            record.failure_message = None
            self._sync_procs.pop(record.pid, None)
            self._pty_procs.pop(record.pid, None)
            self._async_procs.pop(record.pid, None)
            self._descendants.pop(record.pid, None)
            self._record_terminal_state(record)
        self._emit(record, prev, ProcessStatus.EXITED)

    def _mark_killed(
        self, record: ProcessRecord, returncode: int | None = None, *, cause: str = "killed"
    ) -> None:
        with self._status_lock:
            if record.status in _TERMINAL_STATUSES:
                return
            prev = record.status
            record.status = ProcessStatus.KILLED
            record.returncode = returncode
            record.ended_at = datetime.now(tz=UTC)
            record.cause = cause
            record.failure_message = None
            self._sync_procs.pop(record.pid, None)
            self._pty_procs.pop(record.pid, None)
            self._async_procs.pop(record.pid, None)
            self._descendants.pop(record.pid, None)
            self._record_terminal_state(record)
        if cause != "killed":
            logger.debug(f"Process {record.pid} marked KILLED (cause={cause})")
        self._emit(record, prev, ProcessStatus.KILLED)

    def _mark_termination_failed(
        self,
        record: ProcessRecord,
        returncode: int | None = None,
        *,
        reason: str = "Process still alive after kill",
    ) -> None:
        with self._status_lock:
            if record.status in _TERMINAL_STATUSES:
                return
            prev = record.status
            record.status = ProcessStatus.FAILED
            record.returncode = returncode
            record.ended_at = datetime.now(tz=UTC)
            record.cause = "termination_failed"
            record.failure_message = reason
            self._sync_procs.pop(record.pid, None)
            self._pty_procs.pop(record.pid, None)
            self._async_procs.pop(record.pid, None)
            self._descendants.pop(record.pid, None)
            self._record_terminal_state(record)
        logger.error(
            f"Process {record.pid} termination failed: {reason}"
        )
        self._emit(record, prev, ProcessStatus.FAILED)

    def _emit(
        self,
        record: ProcessRecord,
        previous_status: ProcessStatus,
        new_status: ProcessStatus,
    ) -> None:
        event = ProcessEvent(
            record=record,
            previous_status=previous_status,
            new_status=new_status,
            timestamp=datetime.now(tz=UTC),
        )
        for callback in list(self._listeners.values()):
            try:
                callback(event)
            except Exception:
                logger.exception("ProcessManager listener raised an exception")
