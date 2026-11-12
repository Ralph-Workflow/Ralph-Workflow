from __future__ import annotations

import asyncio
import contextlib
import errno
import importlib
import os
import signal
import subprocess
import threading
import time as _time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Protocol, TypeGuard

from loguru import logger

from ralph.process._agent_launch_error import AgentLaunchError
from ralph.process._pty_process import _status_to_returncode
from ralph.process._spawn_argv import sanitize_spawn_command
from ralph.process._spawn_env import child_env_for_spawn
from ralph.process._spawn_validation import prepare_spawn_command, spawn_payload_bytes
from ralph.process.manager._git_termination import git_aware_grace, group_pids
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
    _PsutilProcessLike,
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
from ralph.process.teardown import register_child_session
from ralph.runtime_events import record_runtime_event
from ralph.timeout_defaults import GIT_TERMINATE_GRACE_SECONDS

_PERMITTED_TERMINAL_REASONS = frozenset(
    {
        "conflict_inactivity",
        "operator_cancellation",
        "interactive_completion",
        "quota_exhausted",
        "terminal_startup_error",
    }
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ralph.process.manager.liveness_verifier import LivenessVerifier


def _extract_psutil_returncode(procs: Sequence[object], target_pid: int) -> int | None:
    for p in procs:
        p_pid: object = getattr(p, "pid", None)
        if p_pid == target_pid:
            raw_rc: object = getattr(p, "returncode", None)
            if isinstance(raw_rc, int) and not isinstance(raw_rc, bool):
                return raw_rc
            break
    return None


def _is_own_process_group(pgid: int) -> bool:
    if not hasattr(os, "getpgid"):
        return False
    try:
        return pgid == os.getpgid(0)
    except OSError:
        return True


def _seed_popen_returncode(target: object, rc: int) -> None:
    if _is_popen(target) and target.returncode is None:
        target.returncode = rc


class _ReturncodeSlot(Protocol):
    returncode: int | None


_POPEN_TYPE: Final[type[object]] = subprocess.Popen


def _is_popen(target: object) -> TypeGuard[_ReturncodeSlot]:
    return isinstance(target, _POPEN_TYPE)


def _collect_psutil_exit_pairs(procs: Sequence[object]) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    for p in procs:
        p_pid: object = getattr(p, "pid", None)
        p_rc: object = getattr(p, "returncode", None)
        if isinstance(p_pid, int) and isinstance(p_rc, int) and not isinstance(p_rc, bool):
            pairs.append((p_pid, p_rc))
    return pairs


def _parse_async_term_res(
    term_res: object,
) -> tuple[bool, int | None, list[tuple[int, int]]]:
    if not isinstance(term_res, tuple):
        return False, None, []
    raw_alive = False
    raw_rc: int | None = None
    raw_pairs: list[tuple[int, int]] = []
    try:
        alive_val = term_res[0]
        if isinstance(alive_val, bool):
            raw_alive = alive_val
        rc_val = term_res[1]
        if isinstance(rc_val, int) and not isinstance(rc_val, bool):
            raw_rc = rc_val
        pairs_val = term_res[2]
        if isinstance(pairs_val, list):
            raw_pairs = pairs_val
    except IndexError:
        pass
    return raw_alive, raw_rc, raw_pairs


def _is_reaped_invoke_proc(record: ProcessRecord, proc: object) -> bool:
    if record.label is None or not record.label.startswith("invoke:"):
        return False
    if hasattr(proc, "master_fd"):
        return False
    poll_fn: object = getattr(proc, "poll", None)
    if callable(poll_fn):
        res: object = poll_fn()
        return res is not None
    return False


def _extract_proc_command(proc: object) -> str:
    cmdline_fn: object = getattr(proc, "cmdline", None)
    if callable(cmdline_fn):
        with contextlib.suppress(Exception):
            res: object = cmdline_fn()
            if isinstance(res, (list, tuple)):
                return " ".join(str(x) for x in res)
            if isinstance(res, str) and res:
                return res
    name_fn: object = getattr(proc, "name", None)
    if callable(name_fn):
        with contextlib.suppress(Exception):
            res_name: object = name_fn()
            if isinstance(res_name, str) and res_name:
                return res_name
    return ""


_UNINSPECTABLE_STATUSES: frozenset[str] = frozenset({"no_such_process", "access_denied"})


def _query_proc_status(proc: _PsutilProcessLike, mod: _PsutilModuleLike) -> str:
    try:
        return str(proc.status())
    except mod.NoSuchProcess:
        return "no_such_process"
    except mod.AccessDenied:
        return "access_denied"
    except Exception:
        return "unknown"


def _safe_record_d_state(pid: int) -> None:
    with contextlib.suppress(Exception):
        hcs = importlib.import_module("ralph.mcp.server._host_contention_signal")
        fn: object = getattr(hcs, "record_d_state", None)
        if callable(fn):
            fn(pid)


def _safe_record_sigkill_survivor(pid: int) -> None:
    with contextlib.suppress(Exception):
        hcs = importlib.import_module("ralph.mcp.server._host_contention_signal")
        fn: object = getattr(hcs, "record_sigkill_survivor", None)
        if callable(fn):
            fn(pid)


def _safe_record_host_contention_signal(reason: str) -> None:
    with contextlib.suppress(Exception):
        mhe = importlib.import_module("ralph.mcp.server._mcp_health_event")
        fn: object = getattr(mhe, "record_host_contention_signal", None)
        if callable(fn):
            fn(reason)


def _default_liveness_verifier(
    pid: int,
    *,
    psutil_mod: _PsutilModuleLike | None = None,
) -> LivenessResult:
    return verify_process_liveness(pid, psutil_mod=psutil_mod)


class ProcessManager:
    def __init__(
        self,
        policy: ProcessManagerPolicy | None = None,
        sync_process_factory: _SyncProcessFactory | None = None,
        async_process_factory: _AsyncProcessFactory | None = None,
        pty_process_factory: _PtyProcessFactory | None = None,
        psutil: _PsutilModuleLike | None = None,
        terminate_executor_factory: Callable[[], ThreadPoolExecutor] | None = None,
        liveness_verifier: LivenessVerifier | None = None,
    ) -> None:
        self.policy = policy or ProcessManagerPolicy()
        self._records: dict[int, ProcessRecord] = {}
        self._terminal_records: OrderedDict[int, ProcessRecord] = OrderedDict()
        self._sync_procs: dict[int, _SyncProcessLike] = {}
        self._pty_procs: dict[int, _PtyProcessLike] = {}
        self._descendants: dict[int, list[int]] = {}
        self._termination_outcomes: dict[int, list[dict[str, str]]] = {}
        self._stop_event: threading.Event = threading.Event()
        self._reaper_thread: threading.Thread | None = None

        if self.policy.purge_on_init:
            self._terminal_records.clear()
            self._termination_outcomes.clear()

        self._status_lock = threading.Lock()

        self._async_procs: dict[int, _AsyncProcessLike] = {}
        self._listeners: dict[int, Callable[[ProcessEvent], None]] = {}
        self._listener_counter = 0
        self._terminate_executor: ThreadPoolExecutor | None = None
        self._liveness_verifier: LivenessVerifier = (
            liveness_verifier if liveness_verifier is not None else _default_liveness_verifier
        )
        self._terminate_executor_factory: Callable[[], ThreadPoolExecutor] = (
            terminate_executor_factory
            if terminate_executor_factory is not None
            else self._build_terminate_executor
        )
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

    @staticmethod
    def _has_running_event_loop() -> bool:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return False
        return True

    TERMINATE_EXECUTOR_MAX_WORKERS: int = 4

    def _build_terminate_executor(self) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(
            max_workers=self.TERMINATE_EXECUTOR_MAX_WORKERS,
            thread_name_prefix="ralph-terminate",
        )

    @property
    def terminate_executor(self) -> ThreadPoolExecutor | None:
        return self._terminate_executor

    def _get_terminate_executor(self) -> ThreadPoolExecutor:
        if self._terminate_executor is None:
            self._terminate_executor = self._terminate_executor_factory()
        return self._terminate_executor

    def _note_proc_external_returncode(
        self, record: ProcessRecord, proc: object | None, rc: int
    ) -> None:
        if record.returncode is None:
            record.returncode = rc
        rec_proc: object = getattr(record, "proc", None)
        targets: list[object] = [
            rec_proc,
            proc,
            self._pty_procs.get(record.pid),
            self._sync_procs.get(record.pid),
            self._async_procs.get(record.pid),
        ]
        for target in targets:
            if target is not None:
                target_pid: object = getattr(target, "pid", None)
                if target_pid is not None and target_pid != record.pid:
                    continue
                note_fn: object = getattr(target, "note_external_returncode", None)
                if callable(note_fn):
                    with contextlib.suppress(Exception):
                        note_fn(rc)
                _seed_popen_returncode(target, rc)

    def _reap_sync_and_mark(
        self,
        record: ProcessRecord,
        proc: _SyncProcessLike | _PtyProcessLike,
        cause: str,
    ) -> None:
        last_known_rc: int | None = None
        try:
            if record.status not in _TERMINAL_STATUSES:
                last_known_rc = proc.poll()
                if last_known_rc is None:
                    try:
                        last_known_rc = proc.wait(timeout=0.1)
                    except (subprocess.TimeoutExpired, TimeoutError, OSError):
                        last_known_rc = None
        except (OSError, ProcessLookupError, ChildProcessError):
            last_known_rc = None
        if record.returncode is not None:
            last_known_rc = record.returncode
        if last_known_rc is not None:
            self._note_proc_external_returncode(record, proc, last_known_rc)
        self._mark_killed(record, last_known_rc, cause=cause)

    async def _reap_async_and_mark(
        self,
        record: ProcessRecord,
        proc: _AsyncProcessLike,
        cause: str,
    ) -> None:
        last_known_rc: int | None = None
        try:
            if record.status not in _TERMINAL_STATUSES:
                last_known_rc = proc.returncode
                try:
                    last_known_rc = await asyncio.wait_for(
                        proc.wait(),
                        timeout=0.5,
                    )
                except (TimeoutError, asyncio.CancelledError):
                    last_known_rc = proc.returncode
        except (OSError, ProcessLookupError, ChildProcessError, asyncio.CancelledError):
            last_known_rc = None
        if last_known_rc is None and record.returncode is not None:
            last_known_rc = record.returncode
        if last_known_rc is not None:
            self._note_proc_external_returncode(record, proc, last_known_rc)
        self._mark_killed(record, last_known_rc, cause=cause)

    def _reap_psutil_zombie_and_mark(
        self,
        record: ProcessRecord,
        psutil_proc: _PsutilProcessLike,
        psutil_mod: _PsutilModuleLike,
        cause: str,
    ) -> None:
        harvested_rc: int | None = None
        try:
            if record.status not in _TERMINAL_STATUSES:
                with contextlib.suppress(OSError, ProcessLookupError, ChildProcessError):
                    gone, _ = psutil_mod.wait_procs([psutil_proc], timeout=0.0)
                    harvested_rc = _extract_psutil_returncode(gone, record.pid)
                if (
                    hasattr(os, "waitpid")
                    and hasattr(os, "WNOHANG")
                    and not self._has_running_event_loop()
                ):
                    for _ in range(3):
                        try:
                            reaped_pid, status = os.waitpid(record.pid, os.WNOHANG)
                        except (OSError, ProcessLookupError, ChildProcessError):
                            break
                        if reaped_pid == record.pid:
                            if harvested_rc is None:
                                harvested_rc = _status_to_returncode(status)
                            break
                        if reaped_pid == 0:
                            break
                if harvested_rc is None and record.returncode is not None:
                    harvested_rc = record.returncode
                if harvested_rc is not None:
                    self._note_proc_external_returncode(record, None, harvested_rc)
        except (OSError, ProcessLookupError, ChildProcessError):
            pass
        if harvested_rc is None and record.returncode is not None:
            harvested_rc = record.returncode
        self._mark_killed(record, harvested_rc, cause=cause)

    def _reap_async_in_sync_and_mark(
        self,
        record: ProcessRecord,
        proc: _AsyncProcessLike,
        cause: str,
    ) -> None:
        harvested_rc: int | None = None
        try:
            if record.status not in _TERMINAL_STATUSES:
                if (
                    hasattr(os, "waitpid")
                    and hasattr(os, "WNOHANG")
                    and not self._has_running_event_loop()
                ):
                    for _ in range(3):
                        try:
                            reaped_pid, status = os.waitpid(record.pid, os.WNOHANG)
                        except (OSError, ProcessLookupError, ChildProcessError):
                            break
                        if reaped_pid == record.pid:
                            harvested_rc = _status_to_returncode(status)
                            break
                        if reaped_pid == 0:
                            break
                if harvested_rc is None:
                    raw_rc: object = getattr(proc, "returncode", None)
                    if isinstance(raw_rc, int) and not isinstance(raw_rc, bool):
                        harvested_rc = raw_rc
                if harvested_rc is None and record.returncode is not None:
                    harvested_rc = record.returncode
                if harvested_rc is not None:
                    self._note_proc_external_returncode(record, proc, harvested_rc)
        except (OSError, ProcessLookupError, ChildProcessError):
            if harvested_rc is None:
                err_rc: object = getattr(proc, "returncode", None)
                if isinstance(err_rc, int) and not isinstance(err_rc, bool):
                    harvested_rc = err_rc
        if harvested_rc is None and record.returncode is not None:
            harvested_rc = record.returncode
        self._mark_killed(record, harvested_rc, cause=cause)

    def register_listener(self, callback: Callable[[ProcessEvent], None]) -> Callable[[], None]:
        lid = self._listener_counter
        self._listener_counter += 1
        self._listeners[lid] = callback
        while len(self._listeners) > self.policy.max_listeners:
            oldest = next(iter(self._listeners))
            self._listeners.pop(oldest, None)

        def unsubscribe() -> None:
            self._listeners.pop(lid, None)

        return unsubscribe

    @property
    def listener_count(self) -> int:
        return len(self._listeners)

    def listener_callbacks(self) -> tuple[Callable[[ProcessEvent], None], ...]:
        return tuple(self._listeners.values())

    def register_descendant(self, parent_pid: int, descendant_pid: int) -> None:
        if parent_pid not in self._descendants:
            self._descendants[parent_pid] = []
        if descendant_pid not in self._descendants[parent_pid]:
            self._descendants[parent_pid].append(descendant_pid)

    def _registered_descendants(self, parent_pid: int) -> tuple[int, ...]:
        return tuple(self._descendants.get(parent_pid, ()))

    def zombie_reaper_running(self) -> bool:
        thread = self._reaper_thread
        return thread is not None and thread.is_alive()

    def _terminate_registered_descendants(
        self,
        record: ProcessRecord,
        grace_period_s: float,
    ) -> list[int]:
        descendant_pids: list[int] = self._descendants.pop(record.pid, [])
        if not descendant_pids:
            return []
        psutil_mod = self._psutil
        kill_followup = self.policy.kill_followup_timeout_s
        terminated: list[int] = []
        for descendant_pid in descendant_pids:
            if descendant_pid <= 0:
                continue
            if psutil_mod is not None:
                try:
                    proc = psutil_mod.process_from_pid(descendant_pid)
                except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                    terminated.append(descendant_pid)
                    continue
                try:
                    wait_s = git_aware_grace(grace_period_s, (descendant_pid,))
                    with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                        proc.terminate()
                    gone, _ = self._safe_wait_procs(psutil_mod, [proc], timeout=wait_s)
                    self._harvest_and_mark_descendant(descendant_pid, gone)
                    with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                        proc.kill()
                    still_gone, _ = self._safe_wait_procs(psutil_mod, [proc], timeout=kill_followup)
                    self._harvest_and_mark_descendant(descendant_pid, still_gone)
                    terminated.append(descendant_pid)
                except (OSError, ProcessLookupError):
                    terminated.append(descendant_pid)
                    continue
            else:
                self._terminate_descendant_no_psutil(descendant_pid, grace_period_s)
                terminated.append(descendant_pid)
        if terminated:
            logger.debug(
                f"Process {record.pid}: terminated {len(terminated)} registered "
                f"descendant(s): {terminated}"
            )
        return terminated

    def _harvest_owned_process(self, pid: int, rc: int) -> None:
        desc_rec = self._records.get(pid)
        if desc_rec is not None:
            desc_proc: object = getattr(desc_rec, "proc", None)
            self._note_proc_external_returncode(desc_rec, desc_proc, rc)
            self._mark_killed(desc_rec, rc)
        else:
            handle: object = (
                self._pty_procs.get(pid) or self._sync_procs.get(pid) or self._async_procs.get(pid)
            )
            if handle is not None:
                note_fn: object = getattr(handle, "note_external_returncode", None)
                if callable(note_fn):
                    with contextlib.suppress(Exception):
                        note_fn(rc)

    def _harvest_gone_processes(
        self, gone: Sequence[object], root_pid: int | None = None
    ) -> int | None:
        root_rc: int | None = None
        for p in gone:
            raw_pid: object = getattr(p, "pid", None)
            if not isinstance(raw_pid, int):
                continue
            p_pid: int = raw_pid
            raw_rc: object = getattr(p, "returncode", None)
            if not isinstance(raw_rc, int) or isinstance(raw_rc, bool):
                continue
            p_rc: int = raw_rc
            if root_pid is not None and p_pid == root_pid:
                if root_rc is None:
                    root_rc = p_rc
            else:
                self._harvest_owned_process(p_pid, p_rc)
        return root_rc

    def _harvest_and_mark_descendant(
        self,
        descendant_pid: int,
        gone: Sequence[object],
    ) -> None:
        rc = _extract_psutil_returncode(gone, descendant_pid)
        if rc is not None:
            self._harvest_owned_process(descendant_pid, rc)

    def _terminate_descendant_no_psutil(self, descendant_pid: int, grace_period_s: float) -> None:
        if not hasattr(os, "kill"):
            return
        try:
            os.kill(descendant_pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            return
        with contextlib.suppress(Exception):
            _time.sleep(max(0.0, git_aware_grace(grace_period_s, (descendant_pid,))))
        with contextlib.suppress(OSError, ProcessLookupError):
            os.kill(descendant_pid, signal.SIGKILL)

    def _record_termination_outcome(
        self, pid: int, stage: str, outcome: str, issuer: str | None = None
    ) -> None:
        outcomes = self._termination_outcomes.setdefault(pid, [])
        record = {"stage": stage, "outcome": outcome}
        if issuer is not None:
            record["issuer"] = issuer
        outcomes.append(record)

    def record_terminal_reason(self, pid: int, reason: str, issuer: str | None = None) -> None:
        if reason not in _PERMITTED_TERMINAL_REASONS:
            self._record_termination_outcome(pid, "terminal_reason_refused", reason, issuer)
            return
        outcomes = self._termination_outcomes.setdefault(pid, [])
        if not any(outcome["stage"] == "terminal_reason" for outcome in outcomes):
            record = {"stage": "terminal_reason", "outcome": reason}
            if issuer is not None:
                record["issuer"] = issuer
            outcomes.append(record)

    def original_terminal_reason(self, pid: int) -> str | None:
        for outcome in self._termination_outcomes.get(pid, []):
            if outcome.get("stage") == "terminal_reason":
                return outcome.get("outcome")
        return None

    def termination_issuer(self, pid: int) -> str | None:
        for outcome in reversed(self._termination_outcomes.get(pid, [])):
            if outcome["stage"] in {"terminal_reason", "graceful_terminate", "force_kill"}:
                issuer = outcome.get("issuer")
                return issuer if issuer is not None else outcome["outcome"]
        return None

    def _termination_is_authorized(self, pid: int) -> bool:
        return any(
            outcome["stage"] == "terminal_reason"
            and outcome["outcome"] in _PERMITTED_TERMINAL_REASONS
            for outcome in self._termination_outcomes.get(pid, [])
        )

    def _refuse_unauthorized_termination(self, pid: int) -> bool:
        if self._termination_is_authorized(pid):
            return False
        self._record_termination_outcome(pid, "termination_refused", "missing_permitted_reason")
        logger.warning("Refusing to terminate process {} without a permitted terminal reason", pid)
        return True

    def list_termination_outcomes(self) -> dict[int, list[dict[str, str]]]:
        return dict(self._termination_outcomes)

    def spawn(
        self,
        command: Sequence[str],
        opts: SpawnOptions | None = None,
    ) -> ManagedProcess:
        effective = opts or SpawnOptions()
        cmd = tuple(command)
        now = datetime.now(tz=UTC)
        try:
            cmd = sanitize_spawn_command(cmd, label=effective.label)
            child_env = child_env_for_spawn(
                effective.env,
                allow_activity_relay_controls=effective.allow_activity_relay_controls,
                allow_broker_secret=effective.allow_broker_secret,
                cwd=effective.cwd,
            )
            cmd = prepare_spawn_command(cmd, cwd=effective.cwd, env=child_env)
            proc: _SyncProcessLike = self._sync_process_factory(cmd, effective)
        except (OSError, ValueError) as exc:
            if isinstance(exc, OSError) and exc.errno == errno.E2BIG:
                payload_bytes = spawn_payload_bytes(
                    cmd,
                    child_env_for_spawn(
                        effective.env,
                        allow_activity_relay_controls=effective.allow_activity_relay_controls,
                        allow_broker_secret=effective.allow_broker_secret,
                        cwd=effective.cwd,
                    ),
                )
                record_runtime_event("runtime_launch", str(exc))
                exc = AgentLaunchError(effective.label or cmd[0], exc, payload_bytes)
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
            if isinstance(exc, AgentLaunchError):
                raise exc from exc.__cause__
            raise

        pid = proc.pid
        try:
            pgid = os.getpgid(pid) if hasattr(os, "getpgid") else pid
        except (ProcessLookupError, OSError):
            pgid = pid

        register_child_session(pid)

        record = ProcessRecord(
            pid=pid,
            pgid=pgid,
            command=cmd,
            cwd=effective.cwd,
            started_at=now,
            status=ProcessStatus.RUNNING,
            label=effective.label,
        )
        if (
            self.policy.enable_zombie_reaper
            and self._reaper_thread is None
            and not self._records
            and not self._terminal_records
        ):
            self._start_zombie_reaper()
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
        effective = opts or PtySpawnOptions(cwd=cwd, env=env, cols=cols, rows=rows, label=label)
        cmd = tuple(command)
        now = datetime.now(tz=UTC)
        child_env = child_env_for_spawn(effective.env, cwd=effective.cwd)
        try:
            cmd = sanitize_spawn_command(cmd, label=effective.label)
            cmd = prepare_spawn_command(cmd, cwd=effective.cwd, env=child_env)
            proc = self._pty_process_factory(
                cmd,
                cwd=effective.cwd,
                env=child_env,
                cols=effective.cols,
                rows=effective.rows,
            )
        except (OSError, ValueError) as exc:
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
            if isinstance(exc, OSError) and exc.errno == errno.E2BIG:
                record_runtime_event("runtime_launch", str(exc))
                raise AgentLaunchError(
                    effective.label or cmd[0], exc, spawn_payload_bytes(cmd, child_env)
                ) from exc
            raise

        pid = proc.pid
        try:
            pgid = os.getpgid(pid) if hasattr(os, "getpgid") else pid
        except (ProcessLookupError, OSError):
            pgid = pid

        register_child_session(pid)

        record = ProcessRecord(
            pid=pid,
            pgid=pgid,
            command=cmd,
            cwd=effective.cwd,
            started_at=now,
            status=ProcessStatus.RUNNING,
            label=effective.label,
        )
        if (
            self.policy.enable_zombie_reaper
            and self._reaper_thread is None
            and not self._records
            and not self._terminal_records
        ):
            self._start_zombie_reaper()
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
        effective = opts or SpawnOptions()
        cmd = tuple(command)
        now = datetime.now(tz=UTC)
        child_env = child_env_for_spawn(effective.env, cwd=effective.cwd)
        try:
            cmd = sanitize_spawn_command(cmd, label=effective.label)
            cmd = prepare_spawn_command(cmd, cwd=effective.cwd, env=child_env)
            proc = await self._async_process_factory(
                cmd,
                cwd=effective.cwd,
                env=child_env,
                stdin=effective.stdin,
                stdout=effective.stdout,
                stderr=effective.stderr,
                start_new_session=effective.start_new_session,
            )
        except (OSError, ValueError) as exc:
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
            if isinstance(exc, OSError) and exc.errno == errno.E2BIG:
                record_runtime_event("runtime_launch", str(exc))
                raise AgentLaunchError(
                    effective.label or cmd[0], exc, spawn_payload_bytes(cmd, child_env)
                ) from exc
            raise

        pid = proc.pid
        try:
            pgid = os.getpgid(pid) if hasattr(os, "getpgid") else pid
        except (ProcessLookupError, OSError):
            pgid = pid

        register_child_session(pid)

        record = ProcessRecord(
            pid=pid,
            pgid=pgid,
            command=cmd,
            cwd=effective.cwd,
            started_at=now,
            status=ProcessStatus.RUNNING,
            label=effective.label,
        )
        if (
            self.policy.enable_zombie_reaper
            and self._reaper_thread is None
            and not self._records
            and not self._terminal_records
        ):
            self._start_zombie_reaper()
        self._terminal_records.pop(pid, None)
        self._records[pid] = record
        self._async_procs[pid] = proc
        self._emit(record, ProcessStatus.SPAWNED, ProcessStatus.RUNNING)
        return ManagedAsyncProcess(proc, record, self)

    def list_active(self) -> list[ProcessRecord]:
        return [
            r
            for r in list(self._records.values())
            if r.status in (ProcessStatus.SPAWNED, ProcessStatus.RUNNING)
        ]

    def get_record(self, pid: int, *, include_terminal: bool = True) -> ProcessRecord | None:
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
        self.record_terminal_reason(
            handle.record.pid, "operator_cancellation", "process_manager.terminate"
        )
        gp = grace_period_s if grace_period_s is not None else self.policy.default_grace_period_s
        if isinstance(handle, ManagedProcess):
            self._escalate_termination_sync(handle.record, handle._proc, gp)
            return
        if isinstance(handle, ManagedPtyProcess):
            self._escalate_termination_pty(handle.record, handle._proc, gp)
            return
        if isinstance(handle, ManagedAsyncProcess):
            self._escalate_async_in_sync_context(handle.record, handle._proc, gp)

    def _reconcile_stale_entries(self) -> int:
        reconciled = 0
        for pid in list(self._records.keys()):
            record = self._records.get(pid)
            if record is None or record.status in _TERMINAL_STATUSES:
                continue
            liveness = self._liveness_verifier(pid, psutil_mod=self._psutil)
            if liveness in (LivenessResult.GONE, LivenessResult.UNKNOWN):
                self._mark_killed(record, returncode=None, cause="stale_entry_reconciled")
                logger.debug(f"Stale tracking entry reconciled: PID {pid} no longer exists")
                reconciled += 1
            elif liveness == LivenessResult.ZOMBIE:
                sync_proc = self._sync_procs.get(pid)
                pty_proc = self._pty_procs.get(pid)
                async_proc = self._async_procs.get(pid)
                if sync_proc is not None or pty_proc is not None:
                    reap_target = sync_proc if sync_proc is not None else pty_proc
                    assert reap_target is not None
                    self._reap_sync_and_mark(record, reap_target, cause="zombie_reconciled")
                elif async_proc is not None:
                    self._reap_async_in_sync_and_mark(record, async_proc, cause="zombie_reconciled")
                else:
                    self._mark_killed(record, returncode=None, cause="zombie_reconciled")
                if record.returncode == 0:
                    logger.debug(f"Stale zombie entry reconciled: PID {pid} is zombie")
                else:
                    logger.warning(f"Stale zombie entry reconciled: PID {pid} is zombie")
                reconciled += 1
        return reconciled

    def _start_zombie_reaper(self) -> None:
        if self._reaper_thread is not None:
            return
        if not self.policy.enable_zombie_reaper:
            return
        self._stop_event.clear()
        thread = threading.Thread(
            target=self._zombie_reaper_loop,
            name=f"ProcessManager-zombie-reaper-{id(self)}",
            daemon=True,
        )
        self._reaper_thread = thread
        thread.start()

    def _stop_zombie_reaper(self) -> None:
        thread = self._reaper_thread
        if thread is None:
            return
        self._stop_event.set()
        thread.join(timeout=2.0)
        if self.zombie_reaper_running():
            logger.warning("ProcessManager zombie reaper did not stop within 2.0s")
        self._reaper_thread = None

    def _zombie_reaper_loop(self) -> None:
        interval = self.policy.zombie_reaper_interval_s
        while not self._stop_event.wait(timeout=interval):
            try:
                self._reconcile_stale_entries()
            except Exception:
                logger.exception("ProcessManager zombie reaper iteration failed")

    def cleanup_orphans(
        self,
        handle_or_pid: ManagedProcess | ManagedPtyProcess | ManagedAsyncProcess | int,
        *,
        label_prefix: str | None = None,
    ) -> None:
        if isinstance(handle_or_pid, int):
            if hasattr(os, "killpg"):
                self._kill_pgid(handle_or_pid)
            else:
                self._kill_orphan_tree_windows(handle_or_pid)
            return

        record = handle_or_pid.record
        if hasattr(os, "killpg"):
            self._kill_pgid(record.pgid)
        else:
            self._kill_orphan_tree_windows(record.pid)

        if label_prefix is not None:
            for other_pid, other_record in list(self._records.items()):
                if other_pid == record.pid:
                    continue
                if other_record.label is not None and other_record.label.startswith(label_prefix):
                    self._terminate_by_pid(other_record, self.policy.default_grace_period_s)

    def _kill_pgid(self, pgid: int) -> None:
        if pgid <= 1 or not hasattr(os, "killpg") or _is_own_process_group(pgid):
            return
        members = group_pids(pgid)
        grace_s = git_aware_grace(0.0, members)
        if grace_s > 0:
            with contextlib.suppress(OSError, ProcessLookupError):
                os.killpg(pgid, signal.SIGTERM)
            deadline = _time.monotonic() + grace_s
            while _time.monotonic() < deadline and group_pids(pgid):
                _time.sleep(0.05)
        with contextlib.suppress(OSError, ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)

    def _kill_orphan_tree_windows(self, root_pid: int) -> None:
        psutil_mod = self._psutil
        if psutil_mod is None or root_pid <= 0:
            return
        frontier: set[int] = {root_pid}
        seen: set[int] = set()
        while frontier:
            children_found: dict[int, _PsutilProcessLike] = {}
            with contextlib.suppress(Exception):
                for proc in psutil_mod.process_iter(["pid", "ppid"]):
                    with contextlib.suppress(Exception):
                        info = proc.info
                        ppid = info.get("ppid")
                        pid = proc.pid
                        if ppid in frontier and pid not in seen:
                            children_found[pid] = proc
            if not children_found:
                break
            seen.update(children_found)
            for proc in children_found.values():
                with contextlib.suppress(Exception):
                    proc.kill()
            frontier = set(children_found)

    def _release_git_trees_together(self, grace_period_s: float) -> None:
        psutil_mod = self._psutil
        if psutil_mod is None or grace_period_s >= GIT_TERMINATE_GRACE_SECONDS:
            return
        trees: list[_PsutilProcessLike] = []
        for record in list(self._records.values()):
            if record.status in _TERMINAL_STATUSES:
                continue
            procs = self._find_procs_to_terminate(record, None, psutil_mod)
            if procs and git_aware_grace(0.0, (p.pid for p in procs)) > 0.0:
                trees.extend(procs)
        if not trees:
            return
        for proc in trees:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                proc.terminate()
        _, alive = self._safe_wait_procs(psutil_mod, trees, timeout=GIT_TERMINATE_GRACE_SECONDS)
        for proc in alive:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                proc.kill()

    def shutdown_all(self, *, grace_period_s: float | None = None) -> None:
        gp = grace_period_s if grace_period_s is not None else self.policy.default_grace_period_s

        self._reconcile_stale_entries()
        self._release_git_trees_together(gp)

        first_termination_error: ProcessTerminationError | None = None
        try:
            for pid, record in list(self._records.items()):
                if record.status in _TERMINAL_STATUSES:
                    continue
                try:
                    self.record_terminal_reason(
                        pid, "operator_cancellation", "process_manager.shutdown_all"
                    )
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
                except ProcessTerminationError as exc:
                    if first_termination_error is None:
                        first_termination_error = exc
                    logger.error(
                        "shutdown_all: process {} still alive after kill, continuing with siblings",
                        pid,
                    )
        finally:
            self._stop_zombie_reaper()
            with contextlib.suppress(Exception):
                if self._terminate_executor is not None:
                    self._terminate_executor.shutdown(wait=False)
            self._terminate_executor = None
        if first_termination_error is not None:
            raise first_termination_error

    def drain_completed(self) -> None:
        if self.list_active():
            return
        self._stop_zombie_reaper()

    def shutdown_all_for_label(
        self, label_prefix: str, *, grace_period_s: float | None = None
    ) -> None:
        gp = grace_period_s if grace_period_s is not None else self.policy.default_grace_period_s

        self._reconcile_stale_entries()

        first_termination_error: ProcessTerminationError | None = None
        try:
            for pid, record in list(self._records.items()):
                if (
                    record.label is None
                    or not record.label.startswith(label_prefix)
                    or record.status in _TERMINAL_STATUSES
                ):
                    continue
                try:
                    self.record_terminal_reason(
                        pid, "operator_cancellation", "process_manager.shutdown_all_for_label"
                    )
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
                except ProcessTerminationError as exc:
                    if first_termination_error is None:
                        first_termination_error = exc
                    logger.error(
                        "shutdown_all_for_label({}): process {} still alive after kill, "
                        "continuing with siblings",
                        label_prefix,
                        pid,
                    )
        finally:
            self._stop_zombie_reaper()
        if first_termination_error is not None:
            raise first_termination_error

    def shutdown_all_exec_jobs(self, *, grace_period_s: float | None = None) -> None:
        self.shutdown_all_for_label("exec_job:", grace_period_s=grace_period_s)

    def _terminate_root_only_sync(
        self,
        record: ProcessRecord,
        proc: _SyncProcessLike | _PtyProcessLike,
        grace_period_s: float,
    ) -> None:
        liveness = self._liveness_verifier(record.pid, psutil_mod=self._psutil)
        if liveness == LivenessResult.GONE:
            self._mark_killed(record, None, cause="already_gone")
            logger.debug(f"Process {record.pid} already gone before terminate — marked killed")
            return

        grace_period_s = git_aware_grace(grace_period_s, (record.pid,))
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        logger.debug(f"Process {record.pid} graceful terminate sent, waiting {grace_period_s}s")
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
                post_liveness = self._liveness_verifier(record.pid, psutil_mod=self._psutil)
                if post_liveness == LivenessResult.ZOMBIE:
                    logger.warning(
                        f"Process {record.pid} is zombie after force kill — parent must reap"
                    )
                    self._reap_sync_and_mark(record, proc, cause="zombie_after_kill")
                    return
                if post_liveness != LivenessResult.ALIVE:
                    self._mark_killed(record, proc.poll(), cause="killed")
                    return
                self._mark_termination_failed(record, proc.poll())
                logger.error("Process {} still alive after kill", record.pid)
                raise ProcessTerminationError(
                    record.pid,
                    record.pgid,
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
        liveness = self._liveness_verifier(record.pid, psutil_mod=self._psutil)
        if liveness == LivenessResult.GONE:
            self._mark_killed(record, proc.returncode, cause="already_gone")
            logger.debug(f"Process {record.pid} already gone before terminate — marked killed")
            return

        grace_period_s = git_aware_grace(grace_period_s, (record.pid,))
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        logger.debug(f"Process {record.pid} graceful terminate sent, waiting {grace_period_s}s")
        try:
            rc = await asyncio.wait_for(
                proc.wait(),
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
                    proc.wait(),
                    timeout=self.policy.kill_followup_timeout_s,
                )
            except TimeoutError:
                post_liveness = self._liveness_verifier(record.pid, psutil_mod=self._psutil)
                if post_liveness == LivenessResult.ZOMBIE:
                    logger.warning(
                        f"Process {record.pid} is zombie after force kill — parent must reap"
                    )
                    await self._reap_async_and_mark(record, proc, cause="zombie_after_kill")
                    return
                if post_liveness != LivenessResult.ALIVE:
                    self._mark_killed(record, proc.returncode, cause="killed")
                    return
                self._mark_termination_failed(record, proc.returncode)
                logger.error("Process {} still alive after kill", record.pid)
                raise ProcessTerminationError(
                    record.pid,
                    record.pgid,
                    stage="force_kill",
                    reason="still alive",
                ) from None
        self._mark_killed(record, rc)

    def _safe_wait_procs(
        self,
        psutil_mod: _PsutilModuleLike,
        procs: Sequence[_PsutilProcessLike],
        *,
        timeout: float | None,
    ) -> tuple[list[_PsutilProcessLike], list[_PsutilProcessLike]]:
        try:
            return psutil_mod.wait_procs(procs, timeout=timeout)
        except (OSError, ProcessLookupError, ChildProcessError):
            dead: list[_PsutilProcessLike] = []
            alive: list[_PsutilProcessLike] = []
            for proc in procs:
                raw_pid: object = getattr(proc, "pid", None)
                if not isinstance(raw_pid, int):
                    alive.append(proc)
                    continue
                liveness = self._liveness_verifier(raw_pid, psutil_mod=psutil_mod)
                if liveness in (LivenessResult.GONE, LivenessResult.ZOMBIE):
                    dead.append(proc)
                else:
                    alive.append(proc)
            return dead, alive

    def _reap_untracked_psutil_zombie(
        self,
        psutil_proc: _PsutilProcessLike,
        psutil_mod: _PsutilModuleLike,
    ) -> None:
        pid = psutil_proc.pid
        harvested_rc: int | None = None
        try:
            with contextlib.suppress(OSError, ProcessLookupError, ChildProcessError):
                gone, _ = psutil_mod.wait_procs([psutil_proc], timeout=0.0)
                harvested_rc = _extract_psutil_returncode(gone, pid)
            if (
                hasattr(os, "waitpid")
                and hasattr(os, "WNOHANG")
                and not self._has_running_event_loop()
            ):
                for _ in range(3):
                    try:
                        reaped_pid, status = os.waitpid(pid, os.WNOHANG)
                    except (OSError, ProcessLookupError, ChildProcessError):
                        break
                    if reaped_pid == pid:
                        if harvested_rc is None:
                            harvested_rc = _status_to_returncode(status)
                        break
                    if reaped_pid == 0:
                        break
            if harvested_rc is not None:
                self._harvest_owned_process(pid, harvested_rc)
        except (OSError, ProcessLookupError, ChildProcessError):
            pass

    def _check_and_reap_zombie_survivors(
        self,
        record: ProcessRecord,
        survivors: Sequence[_PsutilProcessLike],
        psutil_mod: _PsutilModuleLike,
        proc: _SyncProcessLike | _PtyProcessLike | None = None,
        harvested_rc: int | None = None,
    ) -> bool:
        any_zombies = False
        all_resolved = True
        for p in survivors:
            pid_liveness = self._liveness_verifier(p.pid, psutil_mod=psutil_mod)
            if pid_liveness == LivenessResult.ZOMBIE:
                any_zombies = True
                logger.warning(f"Process {p.pid} is zombie after force kill — parent must reap")
                if p.pid == record.pid:
                    if proc is not None:
                        self._reap_sync_and_mark(record, proc, cause="zombie_after_kill")
                    else:
                        self._reap_psutil_zombie_and_mark(
                            record, p, psutil_mod, cause="zombie_after_kill"
                        )
                    if record.returncode is None and harvested_rc is not None:
                        record.returncode = harvested_rc
                else:
                    desc_rec = self._records.get(p.pid)
                    if desc_rec is not None:
                        self._reap_psutil_zombie_and_mark(
                            desc_rec, p, psutil_mod, cause="zombie_after_kill"
                        )
                    else:
                        self._reap_untracked_psutil_zombie(p, psutil_mod)
            elif pid_liveness != LivenessResult.GONE:
                all_resolved = False

        if any_zombies and all_resolved:
            root_rc = harvested_rc
            if root_rc is None:
                root_rc = record.returncode
            if root_rc is None and proc is not None:
                with contextlib.suppress(Exception):
                    root_rc = proc.poll()
            if record.status not in _TERMINAL_STATUSES:
                self._terminate_registered_descendants(record, grace_period_s=0.0)
                self._mark_killed(record, root_rc, cause="zombie_after_kill")
            return True
        return False

    def _terminate_by_pid(self, record: ProcessRecord, grace_period_s: float) -> None:
        psutil_mod = self._psutil
        if psutil_mod is None:
            return

        liveness = self._liveness_verifier(record.pid, psutil_mod=psutil_mod)
        if liveness == LivenessResult.GONE:
            self._mark_killed(record, cause="already_gone")
            logger.debug(f"Process {record.pid} already gone before terminate — marked killed")
            return

        all_procs = self._find_procs_to_terminate(record, None, psutil_mod)
        if all_procs is None:
            return

        grace_period_s = git_aware_grace(grace_period_s, (p.pid for p in all_procs))
        for proc in all_procs:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                proc.terminate()
        logger.debug(
            f"Process {record.pid} graceful terminate sent to {len(all_procs)} procs, "
            f"waiting {grace_period_s}s"
        )

        gone, alive = self._safe_wait_procs(psutil_mod, all_procs, timeout=grace_period_s)
        harvested_rc = self._harvest_gone_processes(gone, root_pid=record.pid)
        if harvested_rc is not None:
            self._note_proc_external_returncode(record, None, harvested_rc)
        if alive:
            logger.warning(
                f"Process {record.pid} survived graceful terminate, escalating to force kill"
            )
        for proc in alive:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                proc.kill()

        still_gone, still_alive = self._safe_wait_procs(
            psutil_mod, alive, timeout=self.policy.kill_followup_timeout_s
        )
        still_harvested_rc = self._harvest_gone_processes(still_gone, root_pid=record.pid)
        if harvested_rc is None and still_harvested_rc is not None:
            harvested_rc = still_harvested_rc
            self._note_proc_external_returncode(record, None, harvested_rc)
        if harvested_rc is None:
            harvested_rc = record.returncode
        if still_alive:
            if self._check_and_reap_zombie_survivors(
                record, still_alive, psutil_mod, harvested_rc=harvested_rc
            ):
                return
            self._mark_termination_failed(record, harvested_rc)
            logger.error("Process {} still alive after kill", record.pid)
            raise ProcessTerminationError(
                record.pid,
                record.pgid,
                stage="force_kill",
                reason="still alive",
            )
        self._terminate_registered_descendants(record, grace_period_s)
        self._mark_killed(record, harvested_rc)

    def _find_procs_to_terminate(
        self, record: ProcessRecord, exit_code: int | None, psutil_mod: _PsutilModuleLike
    ) -> list[_PsutilProcessLike] | None:
        try:
            root = psutil_mod.process_from_pid(record.pid)
            return [root, *root.children(recursive=True)]
        except psutil_mod.NoSuchProcess:
            self._mark_killed(record, exit_code)
            return None
        except (psutil_mod.AccessDenied, PermissionError):
            self._raise_access_denied_termination(record, exit_code)
            return None

    def _escalate_termination_sync(
        self,
        record: ProcessRecord,
        proc: _SyncProcessLike | _PtyProcessLike,
        grace_period_s: float,
    ) -> None:
        if record.status in _TERMINAL_STATUSES or self._refuse_unauthorized_termination(record.pid):
            return

        liveness = self._liveness_verifier(record.pid, psutil_mod=self._psutil)
        if liveness == LivenessResult.GONE:
            self._mark_killed(record, proc.poll(), cause="already_gone")
            logger.debug(f"Process {record.pid} already gone before terminate — marked killed")
            return

        psutil_mod = self._psutil
        if psutil_mod is None:
            self._terminate_root_only_sync(record, proc, grace_period_s)
            return

        all_procs = self._find_procs_to_terminate(record, proc.poll(), psutil_mod)
        if all_procs is None:
            return
        self._record_termination_outcome(record.pid, "graceful_terminate", "sent")
        grace_period_s = git_aware_grace(grace_period_s, (record.pid, *(p.pid for p in all_procs)))
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        if _is_reaped_invoke_proc(record, proc):
            self._terminate_registered_descendants(record, grace_period_s)
            self._mark_killed(record, proc.poll())
            return
        for p in all_procs:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                p.terminate()
        logger.debug(
            f"Process {record.pid} graceful terminate sent to {len(all_procs)} procs, "
            f"waiting {grace_period_s}s"
        )

        gone, alive = self._safe_wait_procs(psutil_mod, all_procs, timeout=grace_period_s)
        harvested_rc = self._harvest_gone_processes(gone, root_pid=record.pid)
        if harvested_rc is not None:
            self._note_proc_external_returncode(record, proc, harvested_rc)
        if alive:
            logger.warning(
                f"Process {record.pid} survived graceful terminate, escalating to force kill"
            )
            self._record_termination_outcome(record.pid, "force_kill", "sent")
        for p in alive:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                p.kill()

        still_gone, still_alive = self._safe_wait_procs(
            psutil_mod, alive, timeout=self.policy.kill_followup_timeout_s
        )
        still_harvested_rc = self._harvest_gone_processes(still_gone, root_pid=record.pid)
        if harvested_rc is None and still_harvested_rc is not None:
            harvested_rc = still_harvested_rc
            self._note_proc_external_returncode(record, proc, harvested_rc)
        rc = harvested_rc if harvested_rc is not None else proc.poll()
        if rc is None:
            rc = record.returncode
        if still_alive:
            if self._check_and_reap_zombie_survivors(
                record, still_alive, psutil_mod, proc=proc, harvested_rc=rc
            ):
                return
            self._mark_termination_failed(record, rc)
            _safe_record_sigkill_survivor(record.pid)
            proc_status, proc_cmd = self._get_process_diagnostics(
                record.pid, record=record, psutil_mod=psutil_mod
            )
            logger.error(
                "Process {} still alive after kill (status={}, command={})",
                record.pid,
                proc_status,
                proc_cmd,
            )
            raise ProcessTerminationError(
                record.pid,
                record.pgid,
                stage="force_kill",
                reason="still alive",
            )
        self._terminate_registered_descendants(record, grace_period_s)
        self._mark_killed(record, rc)

    def _escalate_termination_pty(
        self,
        record: ProcessRecord,
        proc: _PtyProcessLike,
        grace_period_s: float,
    ) -> None:
        if record.status in _TERMINAL_STATUSES:
            return
        if self._refuse_unauthorized_termination(record.pid):
            return
        liveness = self._liveness_verifier(record.pid, psutil_mod=self._psutil)
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
        if self._refuse_unauthorized_termination(record.pid):
            return

        liveness = self._liveness_verifier(record.pid, psutil_mod=self._psutil)
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

        def _do_terminate() -> tuple[bool, int | None, list[tuple[int, int]]]:
            harvested_rc: int | None = None
            pairs: list[tuple[int, int]] = []
            try:
                root = psutil_mod.process_from_pid(pid)
                children = root.children(recursive=True)
            except psutil_mod.NoSuchProcess:
                return False, None, []
            except (psutil_mod.AccessDenied, PermissionError) as exc:
                raise PermissionError from exc
            all_procs = [root, *children]
            wait_s = git_aware_grace(grace_period_s, (p.pid for p in all_procs))
            for p in all_procs:
                with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                    p.terminate()
            gone, alive = self._safe_wait_procs(psutil_mod, all_procs, timeout=wait_s)
            for p in alive:
                with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                    p.kill()
            still_gone, still_alive = self._safe_wait_procs(psutil_mod, alive, timeout=policy_kill)
            pairs = _collect_psutil_exit_pairs((*gone, *still_gone))
            for p_pid, p_rc in pairs:
                if p_pid == pid:
                    harvested_rc = p_rc
                    break
            return bool(still_alive), harvested_rc, pairs

        loop = asyncio.get_running_loop()
        harvested_rc: int | None = None
        still_alive: bool = False
        child_pairs: list[tuple[int, int]] = []
        try:
            term_res: object = await loop.run_in_executor(
                self._get_terminate_executor(), _do_terminate
            )
            still_alive, harvested_rc, child_pairs = _parse_async_term_res(term_res)
        except PermissionError:
            self._raise_access_denied_termination(record, proc.returncode)
        for child_pid, child_rc in child_pairs:
            if child_pid != pid:
                self._harvest_owned_process(child_pid, child_rc)
        rc = proc.returncode
        if rc is None and harvested_rc is not None:
            rc = harvested_rc
            self._note_proc_external_returncode(record, proc, harvested_rc)
        if still_alive:
            post_liveness = self._liveness_verifier(record.pid, psutil_mod=psutil_mod)
            if post_liveness == LivenessResult.ZOMBIE:
                logger.warning(f"Process {pid} is zombie after force kill — parent must reap")
                await self._reap_async_and_mark(record, proc, cause="zombie_after_kill")
                return
            self._mark_termination_failed(record, rc)
            proc_status, proc_cmd = self._get_process_diagnostics(
                pid, record=record, psutil_mod=psutil_mod
            )
            logger.error(
                "Process {} still alive after kill (status={}, command={})",
                pid,
                proc_status,
                proc_cmd,
            )
            raise ProcessTerminationError(
                record.pid,
                record.pgid,
                stage="force_kill",
                reason="still alive",
            )
        self._terminate_registered_descendants(record, grace_period_s)
        self._mark_killed(record, rc)

    def _escalate_with_psutil(
        self,
        record: ProcessRecord,
        proc: _AsyncProcessLike,
        psutil_mod: _PsutilModuleLike,
        grace_period_s: float,
    ) -> None:
        all_procs = self._find_procs_to_terminate(record, proc.returncode, psutil_mod)
        if all_procs is None:
            return
        grace_period_s = git_aware_grace(grace_period_s, (p.pid for p in all_procs))
        for p in all_procs:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                p.terminate()
        logger.debug(
            f"Process {record.pid} graceful terminate sent to {len(all_procs)} procs, "
            f"waiting {grace_period_s}s"
        )
        gone, alive = self._safe_wait_procs(psutil_mod, all_procs, timeout=grace_period_s)
        harvested_rc = self._harvest_gone_processes(gone, root_pid=record.pid)
        if harvested_rc is not None:
            self._note_proc_external_returncode(record, proc, harvested_rc)
            if record.returncode is None:
                record.returncode = harvested_rc
        if alive:
            logger.warning(
                f"Process {record.pid} survived graceful terminate, escalating to force kill"
            )
        for p in alive:
            with contextlib.suppress(psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                p.kill()
        still_gone, still_alive = self._safe_wait_procs(
            psutil_mod, alive, timeout=self.policy.kill_followup_timeout_s
        )
        still_harvested_rc = self._harvest_gone_processes(still_gone, root_pid=record.pid)
        if harvested_rc is None:
            harvested_rc = still_harvested_rc
            if harvested_rc is not None:
                self._note_proc_external_returncode(record, proc, harvested_rc)
                if record.returncode is None:
                    record.returncode = harvested_rc
        elif record.returncode is None:
            record.returncode = harvested_rc
        rc = proc.returncode
        if rc is None:
            rc = harvested_rc
        if rc is None:
            rc = record.returncode
        if still_alive:
            if self._check_and_reap_zombie_survivors(
                record, still_alive, psutil_mod, harvested_rc=rc
            ):
                return
            self._mark_termination_failed(record, rc)
            proc_status, proc_cmd = self._get_process_diagnostics(
                record.pid, record=record, psutil_mod=psutil_mod
            )
            logger.error(
                "Process {} still alive after kill (status={}, command={})",
                record.pid,
                proc_status,
                proc_cmd,
            )
            raise ProcessTerminationError(
                record.pid, record.pgid, stage="force_kill", reason="still alive"
            )
        self._terminate_registered_descendants(record, grace_period_s)
        self._mark_killed(record, rc)

    def _escalate_without_psutil(self, record: ProcessRecord, proc: _AsyncProcessLike) -> None:
        grace_s = git_aware_grace(0.0, (record.pid,))
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.terminate()
        deadline = _time.monotonic() + grace_s
        while (
            grace_s > 0
            and _time.monotonic() < deadline
            and self._liveness_verifier(record.pid, psutil_mod=None) != LivenessResult.GONE
        ):
            _time.sleep(0.05)
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        probe_result = self._liveness_verifier(record.pid, psutil_mod=None)
        if probe_result == LivenessResult.GONE:
            self._terminate_registered_descendants(record, grace_period_s=0.0)
            self._mark_killed(record, None)
            return
        if probe_result == LivenessResult.ZOMBIE:
            logger.warning(f"Process {record.pid} is zombie after force kill — parent must reap")
            self._reap_async_in_sync_and_mark(record, proc, cause="zombie_after_kill")
            self._terminate_registered_descendants(record, grace_period_s=0.0)
            return
        self._terminate_registered_descendants(record, grace_period_s=0.0)
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
        if self._refuse_unauthorized_termination(record.pid):
            return
        liveness = self._liveness_verifier(record.pid, psutil_mod=self._psutil)
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
            record.pid,
            record.pgid,
            stage="access_denied",
            reason="Access denied while terminating process",
        )

    def _record_terminal_state(self, record: ProcessRecord) -> None:
        self._records.pop(record.pid, None)
        limit = max(self.policy.terminal_history_limit, 0)
        if limit == 0:
            self._terminal_records.clear()
            self._termination_outcomes.pop(record.pid, None)
            return
        self._terminal_records.pop(record.pid, None)
        self._terminal_records[record.pid] = record
        while len(self._terminal_records) > limit:
            evicted_pid, _ = self._terminal_records.popitem(last=False)
            self._termination_outcomes.pop(evicted_pid, None)

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
            if returncode is not None or record.returncode is None:
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

    def _get_process_diagnostics(
        self,
        pid: int,
        *,
        record: ProcessRecord | None = None,
        psutil_mod: _PsutilModuleLike | None = None,
    ) -> tuple[str, str]:
        mod = psutil_mod if psutil_mod is not None else self._psutil
        status = "unknown"
        cmd = " ".join(record.command) if record is not None and record.command else ""

        if mod is not None and pid > 0:
            try:
                proc = mod.process_from_pid(pid)
                status = _query_proc_status(proc, mod)
                if status not in _UNINSPECTABLE_STATUSES:
                    proc_cmd = _extract_proc_command(proc)
                    if proc_cmd:
                        cmd = proc_cmd
            except mod.NoSuchProcess:
                status = "no_such_process"
            except mod.AccessDenied:
                status = "access_denied"
            except Exception:
                status = "unknown"

        if not cmd:
            cmd = "unknown"

        return status, cmd

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
            if returncode is not None or record.returncode is None:
                record.returncode = returncode
            record.ended_at = datetime.now(tz=UTC)
            record.cause = "termination_failed"
            record.failure_message = reason
            self._sync_procs.pop(record.pid, None)
            self._pty_procs.pop(record.pid, None)
            self._async_procs.pop(record.pid, None)
            self._descendants.pop(record.pid, None)
            self._record_terminal_state(record)
        proc_status, proc_cmd = self._get_process_diagnostics(
            record.pid, record=record, psutil_mod=self._psutil
        )
        logger.error(
            "Process {} termination failed: {} (status={}, command={})",
            record.pid,
            reason,
            proc_status,
            proc_cmd,
        )
        with contextlib.suppress(Exception):
            _safe_record_d_state(record.pid)
            _safe_record_sigkill_survivor(record.pid)
            if proc_status.startswith("D") or proc_status == "disk-sleep":
                _safe_record_host_contention_signal(f"d_state:{proc_status}")
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
