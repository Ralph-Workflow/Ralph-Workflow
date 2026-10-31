"""Exec job registry and runner for long-running MCP commands.

Manages long-running exec jobs with fuzzy liveness monitoring, bounded
resource consumption, request-scoped progress subscriptions, and shutdown seams.
"""

from __future__ import annotations

import collections
import contextlib
import dataclasses
import importlib
import io
import logging
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import IO, TYPE_CHECKING, Final, Literal, TypeGuard

import psutil

import ralph.timeout_defaults as td
from ralph.mcp.tools._exec_job_output import (
    _append_tail_buffer,
    _format_job_output,
    _merge_stream_buffers,
    serialize_liveness_verdict,
)
from ralph.process.manager import ProcessManager, SpawnOptions, get_process_manager
from ralph.process.manager._liveness_assessor import (
    LivenessConfig,
    LivenessVerdict,
    StallIntegrator,
    SuspicionClock,
    assess,
    default_liveness_config,
)
from ralph.process.manager._liveness_sampler import (
    OutputEvidence,
    TreeSampler,
    _LivenessProc,
    _PsutilModuleLike,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ralph.process.manager._managed_process import ManagedProcess

logger = logging.getLogger(__name__)

KillCause = Literal[
    "stalled", "no_progress", "hard_cap", "abandoned", "session_end", "restart_lost"
]
_MAX_UNTERMINATED_LINE_BYTES = 1000
# Bound on how long a finishing job waits for its reader threads to hit EOF.
_READER_DRAIN_TIMEOUT_S = 1.0

# Signature of the runtime hook registrars cached in ``_runtime_hook_cache``.
_HookRegistrar = Callable[[Callable[[], None]], None]

# Module-level lazy initialization of the runtime hook registrars, kept
# here (after every top-level import) so the import order is preserved
# while still breaking the ``runtime`` ↔ ``_fallback_http_handler`` ↔
# ``exec_sse_streaming`` ↔ ``_exec_jobs`` circular-import cycle.
# A small bounded cache holds the resolved registrars after first lookup
# so they aren't re-fetched on every registry construction.
_runtime_hook_cache: dict[
    str, _HookRegistrar
] = {}  # bounded-accumulator-ok: cache holds at most 2 entries (owner_loss + shutdown) and is read-once after first hit


def _is_hook_registrar(value: object) -> TypeGuard[_HookRegistrar]:
    """Narrow an untyped runtime attribute to a hook registrar callable."""
    return callable(value)


def _hook_registrar(module: object, attribute: str) -> _HookRegistrar:
    """Narrow one runtime hook registrar read across the module boundary.

    The registrars live on ``ralph.mcp.server.runtime`` and are fetched
    through the import machinery to break the import cycle, so the value
    arrives untyped. This is the registered boundary for that read: it
    checks the attribute really is a callable registrar and raises rather
    than asserting the shape.
    """
    registrar: object = getattr(module, attribute)
    if not _is_hook_registrar(registrar):
        raise TypeError(f"{attribute} is not a hook registrar callable")
    return registrar


def _ensure_runtime_hooks() -> tuple[_HookRegistrar, _HookRegistrar]:
    """Lazily import the runtime hook registrars to break the import cycle.

    ``_exec_jobs`` is imported by ``exec_sse_streaming``, which is imported by
    ``_fallback_http_handler``, which is imported by ``_fallback_standalone_server``,
    which is imported by ``runtime``. Importing ``runtime`` at module top
    level races against the reverse chain at module init. The runtime hook
    registrars are simple list-appending callbacks without side effects on
    import, so deferring the lookup to first use preserves the public
    contract while removing the cycle.
    """
    cached_owner_loss = _runtime_hook_cache.get("owner_loss")
    cached_shutdown = _runtime_hook_cache.get("shutdown")
    if cached_owner_loss is None or cached_shutdown is None:
        runtime_mod = importlib.import_module("ralph.mcp.server.runtime")
        cached_owner_loss = _hook_registrar(runtime_mod, "register_owner_loss_hook")
        cached_shutdown = _hook_registrar(runtime_mod, "register_shutdown_hook")
        _runtime_hook_cache["owner_loss"] = cached_owner_loss
        _runtime_hook_cache["shutdown"] = cached_shutdown
    return cached_owner_loss, cached_shutdown


class _PsutilLivenessProc:
    """Typed view of a ``psutil.Process`` satisfying ``_LivenessProc`` on every platform.

    psutil has no ``io_counters`` on macOS; the sampler treats that as no I/O evidence.
    """

    def __init__(self, proc: psutil.Process) -> None:
        self._proc = proc

    @property
    def pid(self) -> int:
        return self._proc.pid

    def children(self, recursive: bool = False) -> Sequence[_LivenessProc]:
        return [_PsutilLivenessProc(c) for c in self._proc.children(recursive=recursive)]

    def cpu_times(self) -> object:
        return self._proc.cpu_times()

    def io_counters(self) -> object:
        if sys.platform != "darwin":
            return self._proc.io_counters()
        raise AttributeError("psutil does not provide io_counters on macOS")

    def num_ctx_switches(self) -> object:
        return self._proc.num_ctx_switches()

    def create_time(self) -> float:
        return self._proc.create_time()


class _DefaultPsutilModule:
    """Default psutil module wrapper satisfying _PsutilModuleLike."""

    def __init__(self) -> None:
        self.Process: Callable[[int], _LivenessProc] = self._get_process

    def _get_process(self, pid: int) -> _LivenessProc:
        return _PsutilLivenessProc(psutil.Process(pid))


_DEFAULT_PSUTIL_MODULE = _DefaultPsutilModule()


@dataclass
class _JobSnapshot:
    """Snapshot of a currently running exec job."""

    job_id: str
    running: bool
    elapsed_s: float
    output: dict[str, object]
    verdict: LivenessVerdict | None
    instruction: str


@dataclass
class _JobResult:
    """Final result of a completed, cancelled, or killed exec job."""

    job_id: str
    returncode: int | None
    output: dict[str, object]
    kill_cause: KillCause | None
    verdict: LivenessVerdict | None
    elapsed_s: float
    cancelled: bool = False
    #: Effective runtime ceiling in ms (clamped ``max_runtime_ms`` else ``EXEC_JOB_CEILING_S``).
    ceiling_ms: int | None = None


@dataclass
class _ProgressSubscription:
    """Request-scoped subscription to job liveness / progress updates."""

    request_id: str
    progress_token: str
    sink: Callable[[dict[str, object]], None]
    job_id: str | None = None


class _ExecJobError(Exception):
    """Failure raised for invalid job operations or missing jobs."""

    def __init__(self, job_id: str, reason: str) -> None:
        self.job_id = job_id
        self.reason = reason
        super().__init__(f"Job {job_id}: {reason}" if job_id else reason)


JobSnapshot = _JobSnapshot
JobResult = _JobResult
ProgressSubscription = _ProgressSubscription
ExecJobError = _ExecJobError


class _ActiveJob:
    """Internal state for an actively managed exec job."""

    def __init__(
        self,
        job_id: str,
        command: Sequence[str],
        capability: str,
        cwd: str | None,
        timeout_ms: int,
        max_runtime_ms: int | None,
        clock: Callable[[], float],
        liveness_config: LivenessConfig,
        psutil_module: _PsutilModuleLike,
        spill_dir: Path | None = None,
    ) -> None:
        self.job_id = job_id
        self.command = tuple(command)
        self.capability = capability
        self.cwd = cwd
        self.timeout_ms = timeout_ms
        self.max_runtime_ms = max_runtime_ms
        self.clock = clock
        self.liveness_config = liveness_config
        self.psutil_module = psutil_module
        self.spill_dir = spill_dir

        self.start_time = clock()
        self.last_wait_time = self.start_time
        self.last_sample_time = self.start_time
        # Effective runtime ceiling (D10): the per-call ``max_runtime_ms``
        # clamped to ``EXEC_JOB_CEILING_MAX_S``, else the default
        # ``EXEC_JOB_CEILING_S``. Resolved once here so the kill check and the
        # kill message can never name two different limits.
        if max_runtime_ms is not None:
            ceiling_s = min(max_runtime_ms / 1000.0, td.EXEC_JOB_CEILING_MAX_S)
        else:
            ceiling_s = td.EXEC_JOB_CEILING_S
        self.ceiling_ms = int(ceiling_s * 1000)
        # ``last_output_at`` is initialised to ``None`` (no output yet) so the
        # first sample sees ``seconds_since_output=None`` → OutputRecent=0
        # → R1/R4/R7 do NOT fire → R5 fires alone → score=0. Initialising to
        # ``self.start_time`` would credit the command with fresh-output
        # membership at t=0 and stall pressure would drain for the first
        # ``output_recent_max_s`` seconds; an idle command from birth would
        # then be killed at ``stall_window + output_recent_max_s`` instead
        # of the requested ``stall_window``. The bug is reproduced by
        # ``tests/test_exec_jobs.py::test_default_idle_kills_at_90s`` (see
        # WU-3 Wt-19 EV-002 in the analysis).
        self.last_output_at: float | None = None
        self.suspicious_started_at: float | None = None

        self.lock = threading.Lock()
        self.done_event = threading.Event()

        self.handle: ManagedProcess | None = None
        self.running = True
        self.cancelled = False
        self.kill_cause: KillCause | None = None
        self.returncode: int | None = None
        self.latest_verdict: LivenessVerdict | None = LivenessVerdict(
            score=0.0,
            pressure=0.0,
            stall_window_s=timeout_ms / 1000.0,
            strongest_rule="",
            suspicious=False,
            verdict_kind="working",
            signals={},
            elapsed_s=0.0,
        )
        self.suspicion_clock = SuspicionClock(threshold_s=td.EXEC_SUSPICIOUS_REPORT_S)

        self.stdout_head = bytearray()
        self.stdout_tail = bytearray()
        self.total_stdout_bytes = 0

        self.stderr_head = bytearray()
        self.stderr_tail = bytearray()
        self.total_stderr_bytes = 0

        self.output_lines: collections.deque[OutputEvidence] = collections.deque(maxlen=1000)
        self.stall_integrator = StallIntegrator(theta=liveness_config.stall_theta)
        self.tree_sampler = TreeSampler(psutil_module, clock, liveness_config)
        self.runner_thread: threading.Thread | None = None
        self.watcher_thread: threading.Thread | None = None
        self.reader_threads: tuple[threading.Thread, ...] = ()


class ExecJobRegistry:
    """Registry and coordinator for long-running exec jobs."""

    def __init__(
        self,
        *,
        process_manager: ProcessManager | None = None,
        clock: Callable[[], float] | None = None,
        liveness_config: LivenessConfig | None = None,
        psutil_module: _PsutilModuleLike | None = None,
        sample_interval_s: float = td.EXEC_LIVENESS_SAMPLE_INTERVAL_S,
        auto_register_runtime_hooks: bool = True,
        background_sampler: bool = True,
        spill_dir: Path | None = None,
        reader_drain_timeout_s: float = _READER_DRAIN_TIMEOUT_S,
    ) -> None:
        self._pm = process_manager if process_manager is not None else get_process_manager()
        self._clock = clock if clock is not None else time.monotonic
        self._liveness_config = (
            liveness_config if liveness_config is not None else default_liveness_config()
        )
        self._psutil_module: _PsutilModuleLike = (
            psutil_module if psutil_module is not None else _DEFAULT_PSUTIL_MODULE
        )
        self._sample_interval_s = sample_interval_s
        self._background_sampler = background_sampler
        self._spill_dir = spill_dir
        self._reader_drain_timeout_s = reader_drain_timeout_s

        self._lock = threading.Lock()
        self._running_jobs: dict[
            str, _ActiveJob
        ] = {}  # bounded-accumulator-ok: max EXEC_MAX_RUNNING_JOBS
        self._finished_results: collections.deque[JobResult] = collections.deque(
            maxlen=td.EXEC_JOB_RESULT_FIFO
        )  # bounded-accumulator-ok: FIFO cap
        self._finished_capabilities: dict[str, str] = {}  # bounded-accumulator-ok: capped by FIFO
        self._subscriptions: dict[
            str, ProgressSubscription
        ] = {}  # bounded-accumulator-ok: request-scoped

        if auto_register_runtime_hooks:
            _register_shutdown, _register_owner_loss = _ensure_runtime_hooks()
            _register_shutdown(lambda: self.shutdown_all("session_end"))
            _register_owner_loss(lambda: self.shutdown_all("session_end"))

    def is_running(self, job_id: str) -> bool:
        """Return whether the job is still active in this registry."""
        with self._lock:
            return job_id in self._running_jobs

    def note_output(self, job_id: str, *, capability: str) -> None:
        """Record that the job produced output now (refreshes the stall window)."""
        with self._lock:
            job = self._lookup_job_locked(job_id, capability)
            if not isinstance(job, JobResult):
                job.last_output_at = self._clock()

    def last_finished_result(self) -> JobResult | None:
        """Return the most recently published finished result, if any."""
        with self._lock:
            return self._finished_results[-1] if self._finished_results else None

    def subscribe_progress(self, sub: ProgressSubscription) -> None:
        """Subscribe a request-scoped sink to progress updates."""
        with self._lock:
            self._subscriptions[sub.request_id] = sub

    def unsubscribe_progress(self, request_id: str) -> None:
        """Unsubscribe a request-scoped sink. Idempotent."""
        with self._lock:
            self._subscriptions.pop(request_id, None)

    def _render_job_output(self, job: _ActiveJob) -> dict[str, object]:
        """Construct a formatted output dictionary for an active or finished job."""
        with job.lock:
            stdout_bytes, stdout_cut = _merge_stream_buffers(
                job.stdout_head, job.stdout_tail, job.total_stdout_bytes
            )
            stderr_bytes, stderr_cut = _merge_stream_buffers(
                job.stderr_head, job.stderr_tail, job.total_stderr_bytes
            )
            total_stdout = job.total_stdout_bytes
            total_stderr = job.total_stderr_bytes

        return _format_job_output(
            stdout_bytes,
            stderr_bytes,
            total_stdout_len=total_stdout,
            total_stderr_len=total_stderr,
            truncated=stdout_cut or stderr_cut,
            spill_dir=job.spill_dir,
        )

    def _dispatch_progress_for_job(self, job: _ActiveJob) -> None:
        """Invoke active progress sinks for this job."""
        with self._lock:
            matching_subs = [
                sub for sub in self._subscriptions.values() if sub.job_id == job.job_id
            ]

        if not matching_subs:
            return

        now = self._clock()
        verdict = job.latest_verdict
        payload = {
            "job_id": job.job_id,
            "elapsed_s": max(0.0, now - job.start_time),
            "verdict": verdict,
            "verdict_kind": verdict.verdict_kind if verdict else "unknown",
            "score": verdict.score if verdict else 0.0,
            "pressure": verdict.pressure if verdict else 0.0,
        }

        for sub in matching_subs:
            sub_payload = dict(payload)
            sub_payload["progressToken"] = sub.progress_token
            try:
                sub.sink(sub_payload)
            except Exception:
                self.unsubscribe_progress(sub.request_id)

    def _teardown_progress_for_job(self, job_id: str) -> None:
        """Remove all subscriptions bound to job_id."""
        with self._lock:
            to_remove = [
                req_id for req_id, sub in self._subscriptions.items() if sub.job_id == job_id
            ]
            for req_id in to_remove:
                self._subscriptions.pop(req_id, None)

    def _settle_job(self, job: _ActiveJob, returncode: int | None, cause: KillCause | None) -> None:
        """Reap orphans, drain readers (bounded), publish the result, then wake waiters.

        Never closes a pipe: ``BufferedReader.close()`` blocks while a reader sits
        in ``read1()``, which a ``setsid`` grandchild that escaped ``cleanup_orphans``
        can prolong indefinitely. Each reader closes its own stream at EOF.
        """
        if job.handle is not None:
            with contextlib.suppress(Exception):
                self._pm.cleanup_orphans(job.handle)
        deadline = time.monotonic() + self._reader_drain_timeout_s
        for reader in job.reader_threads:
            reader.join(timeout=max(0.0, deadline - time.monotonic()))
        if any(reader.is_alive() for reader in job.reader_threads):
            logger.warning(
                "Exec job %s: detached process holds its pipes; output may be partial", job.job_id
            )
        self._publish_finished_result(
            job,
            self._build_job_result(
                job, returncode=returncode, kill_cause=cause, cancelled=job.cancelled
            ),
        )
        job.done_event.set()

    def _build_job_result(
        self,
        job: _ActiveJob,
        *,
        returncode: int | None,
        kill_cause: KillCause | None,
        cancelled: bool,
    ) -> JobResult:
        """Render the terminal :class:`JobResult` for a job that has stopped."""
        now = self._clock()
        return JobResult(
            job_id=job.job_id,
            returncode=returncode,
            output=self._render_job_output(job),
            kill_cause=kill_cause,
            verdict=job.latest_verdict,
            elapsed_s=max(0.0, now - job.start_time),
            cancelled=cancelled,
            ceiling_ms=job.ceiling_ms,
        )

    def _publish_finished_result(self, job: _ActiveJob, result: JobResult) -> None:
        """Move a stopped job out of the running set into the bounded result FIFO."""
        with self._lock:
            self._running_jobs.pop(job.job_id, None)
            self._finished_results.append(result)
            self._finished_capabilities[job.job_id] = job.capability
            # FIFO prune capabilities dict
            valid_ids = {r.job_id for r in self._finished_results}
            self._finished_capabilities = {
                k: v for k, v in self._finished_capabilities.items() if k in valid_ids
            }
        self._teardown_progress_for_job(job.job_id)

    def _kill_job(self, job: _ActiveJob, cause: KillCause | None) -> None:
        """Forcibly terminate a running job process tree and record final result."""
        with self._lock:
            if not job.running:
                return
            job.running = False
            job.kill_cause = cause

        if job.handle is not None:
            with contextlib.suppress(Exception):
                job.handle.terminate(grace_period_s=0.0)
        self._settle_job(job, job.returncode, cause)

    def _mark_finished(self, job: _ActiveJob, returncode: int) -> None:
        """Record normal process exit and reap orphaned descendants."""
        with self._lock:
            if not job.running:
                return
            job.running = False
            job.returncode = returncode
            job.kill_cause = None

        self._settle_job(job, returncode, None)

    def step_job(self, job_id: str, dt: float | None = None) -> None:
        """Perform one liveness sampling, assessment, and kill check iteration."""
        with self._lock:
            job = self._running_jobs.get(job_id)
        if job is None or not job.running:
            return

        now = self._clock()
        step_dt = max(0.0, now - job.last_sample_time) if dt is None else dt
        job.last_sample_time = now

        # 1. Process exit check (root exit)
        if job.handle is not None:
            rc = job.handle.poll()
            if rc is not None:
                self._mark_finished(job, returncode=rc)
                return

        # 2. Abandonment check
        if now - job.last_wait_time >= td.EXEC_JOB_ABANDON_S:
            self._kill_job(job, cause="abandoned")
            return

        # 3. Hard cap check
        if now - job.start_time >= job.ceiling_ms / 1000.0:
            self._kill_job(job, cause="hard_cap")
            return

        # 4. Sample liveness. The sampler prunes records older than the evidence
        # window before computing novelty, so a historic burst of distinct
        # startup lines can no longer mask a current repetitive-output stall.
        pid = job.handle.pid if job.handle is not None else 0
        with job.lock:
            samples = list(job.output_lines)
            last_out = job.last_output_at

        window = job.tree_sampler.sample(pid, last_out, samples)
        raw_verdict = assess(window, job.liveness_config)

        # 5. Stall check
        stall_window_s = job.timeout_ms / 1000.0
        new_pressure = job.stall_integrator.step(raw_verdict.score, step_dt)

        # 6. Suspicion clock
        job.suspicion_clock.step(strongest_rule=raw_verdict.strongest_rule, dt=step_dt)

        verdict = dataclasses.replace(
            raw_verdict,
            pressure=new_pressure,
            stall_window_s=stall_window_s,
        )
        job.latest_verdict = verdict

        logger.info(
            "Exec job %s sample: score=%.2f pressure=%.2f/%.1f rule=%s kind=%s signals=%s",
            job.job_id,
            verdict.score,
            verdict.pressure,
            verdict.stall_window_s,
            verdict.strongest_rule,
            verdict.verdict_kind,
            verdict.signals,
        )

        if job.stall_integrator.should_kill(stall_window_s):
            self._kill_job(job, cause="stalled")
            return

        if job.suspicion_clock.streak_s >= td.EXEC_NO_PROGRESS_WINDOW_S:
            self._kill_job(job, cause="no_progress")
            return

        # 7. Progress notifications
        self._dispatch_progress_for_job(job)

    def _read_stream(self, job: _ActiveJob, stream: IO[bytes] | None, is_stdout: bool) -> None:
        """Reader thread: drain one pipe, then close it from this thread at EOF."""
        if stream is None:
            return
        buffer_line = bytearray()
        try:
            while True:
                try:
                    chunk = (
                        stream.read1(8192)
                        if isinstance(stream, io.BufferedIOBase)
                        else stream.read(8192)
                    )
                except Exception:
                    break
                if not chunk:
                    break
                now = self._clock()
                with job.lock:
                    job.last_output_at = now
                    if is_stdout:
                        job.total_stdout_bytes += len(chunk)
                        _append_tail_buffer(job.stdout_head, job.stdout_tail, chunk)
                    else:
                        job.total_stderr_bytes += len(chunk)
                        _append_tail_buffer(job.stderr_head, job.stderr_tail, chunk)

                    buffer_line.extend(chunk)
                    while b"\n" in buffer_line:
                        line_bytes, _, rest = buffer_line.partition(b"\n")
                        buffer_line = bytearray(rest)
                        decoded = line_bytes.decode("utf-8", errors="replace").strip()
                        if decoded:
                            job.output_lines.append(OutputEvidence(now, decoded))
                    if len(buffer_line) > _MAX_UNTERMINATED_LINE_BYTES:
                        decoded = buffer_line.decode("utf-8", errors="replace").strip()
                        job.output_lines.append(OutputEvidence(now, decoded))
                        buffer_line = bytearray()
        finally:
            with contextlib.suppress(Exception):
                stream.close()

    def _watch_job(self, job: _ActiveJob, handle: ManagedProcess) -> None:
        """Watch process exit and mark the job finished."""
        while job.running and not job.done_event.is_set():
            try:
                rc = handle.poll()
            except Exception:
                rc = None
            if rc is not None:
                self._mark_finished(job, returncode=rc)
                break
            if job.done_event.wait(timeout=0.02):
                break

    def _runner_loop(self, job: _ActiveJob) -> None:
        """Periodic background runner loop for active job."""
        while not job.done_event.wait(timeout=self._sample_interval_s):
            if job.done_event.is_set():
                break
            self.step_job(job.job_id)

    @staticmethod
    def _spawn_daemon(target: Callable[[], None], name: str) -> threading.Thread:
        """Start and return a named daemon thread running ``target``."""
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        return thread

    def start(
        self,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        timeout_ms: int = td.EXEC_DEFAULT_TIMEOUT_MS,
        max_runtime_ms: int | None = None,
        capability: str,
        progress: ProgressSubscription | None = None,
    ) -> str:
        """Spawn and register a long-running exec job."""
        job_id = f"job_{secrets.token_urlsafe(12)}"
        active = _ActiveJob(
            job_id=job_id,
            command=command,
            capability=capability,
            cwd=cwd,
            timeout_ms=timeout_ms,
            max_runtime_ms=max_runtime_ms,
            clock=self._clock,
            liveness_config=self._liveness_config,
            psutil_module=self._psutil_module,
            spill_dir=self._spill_dir,
        )

        with self._lock:
            if len(self._running_jobs) >= td.EXEC_MAX_RUNNING_JOBS:
                raise ExecJobError(
                    "", f"Running job limit exceeded (max {td.EXEC_MAX_RUNNING_JOBS})"
                )
            self._running_jobs[job_id] = active

        if progress is not None:
            progress.job_id = job_id
            self.subscribe_progress(progress)

        try:
            handle = self._pm.spawn(
                command,
                SpawnOptions(
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    label=f"exec_job:{job_id}",
                ),
            )
            active.handle = handle
        except Exception as exc:
            with self._lock:
                self._running_jobs.pop(job_id, None)
            self._teardown_progress_for_job(job_id)
            raise ExecJobError(job_id, f"Failed to spawn job: {exc}") from exc

        active.reader_threads = (
            self._spawn_daemon(
                partial(self._read_stream, active, handle.stdout, True), f"job-stdout-{job_id}"
            ),
            self._spawn_daemon(
                partial(self._read_stream, active, handle.stderr, False), f"job-stderr-{job_id}"
            ),
        )

        if self._background_sampler:
            active.watcher_thread = self._spawn_daemon(
                partial(self._watch_job, active, handle),
                f"job-watch-{job_id}",
            )
            active.runner_thread = self._spawn_daemon(
                partial(self._runner_loop, active), f"job-runner-{job_id}"
            )

        return job_id

    def _lookup_job_locked(self, job_id: str, capability: str) -> _ActiveJob | JobResult:
        """Resolve ``job_id``, verifying the caller's capability.

        Returns the live :class:`_ActiveJob` or its terminal :class:`JobResult`
        when it has already finished. Raises :class:`ExecJobError` for an
        unknown/expired job or a capability mismatch. The caller must hold
        ``self._lock``.
        """
        active = self._running_jobs.get(job_id)
        if active is not None:
            if active.capability != capability:
                raise ExecJobError(
                    job_id,
                    f"capability mismatch: expected {active.capability}, got {capability}",
                )
            return active

        for res in reversed(self._finished_results):
            if res.job_id != job_id:
                continue
            expected_cap = self._finished_capabilities.get(job_id)
            if expected_cap is not None and expected_cap != capability:
                raise ExecJobError(
                    job_id,
                    f"capability mismatch: expected {expected_cap}, got {capability}",
                )
            return res
        raise ExecJobError(
            job_id,
            "unknown or expired job; process was killed or lost in server restart",
        )

    def wait(
        self,
        job_id: str,
        *,
        wait_ms: int,
        capability: str,
        progress: ProgressSubscription | None = None,
    ) -> JobSnapshot | JobResult:
        """Wait up to wait_ms for job completion; return JobResult or JobSnapshot."""
        with self._lock:
            found = self._lookup_job_locked(job_id, capability)
            if isinstance(found, JobResult):
                return found
            active = found
            active.last_wait_time = self._clock()

        if progress is not None:
            progress.job_id = job_id
            self.subscribe_progress(progress)

        try:
            wait_s = max(0.0, wait_ms / 1000.0)
            active.done_event.wait(timeout=wait_s)

            with self._lock:
                if not active.running or active.done_event.is_set():
                    for res in reversed(self._finished_results):
                        if res.job_id == job_id:
                            return res
                    return self._build_job_result(
                        active,
                        returncode=active.returncode,
                        kill_cause=active.kill_cause,
                        cancelled=active.cancelled,
                    )
                else:
                    now = self._clock()
                    return JobSnapshot(
                        job_id=active.job_id,
                        running=True,
                        elapsed_s=max(0.0, now - active.start_time),
                        output=self._render_job_output(active),
                        verdict=active.latest_verdict,
                        instruction="call exec_wait; do not re-run",
                    )
        finally:
            # Abandonment measures think time since this reply, not since the wait began.
            with self._lock:
                active.last_wait_time = self._clock()
            if progress is not None:
                self.unsubscribe_progress(progress.request_id)

    def cancel(self, job_id: str, *, capability: str) -> JobResult:
        """Explicitly cancel a running or completed job."""
        with self._lock:
            found = self._lookup_job_locked(job_id, capability)
            if isinstance(found, JobResult):
                return found
            active = found
            active.cancelled = True

        self._kill_job(active, cause=None)

        with self._lock:
            for res in reversed(self._finished_results):
                if res.job_id == job_id:
                    return res
        return self._build_job_result(active, returncode=None, kill_cause=None, cancelled=True)

    def shutdown_all(self, cause: KillCause = "session_end") -> None:
        """Forcibly kill all active running jobs."""
        with self._lock:
            active_list = list(self._running_jobs.values())

        for active in active_list:
            self._kill_job(active, cause=cause)

        # Defense-in-depth: reap any lingering processes under label prefix
        with contextlib.suppress(Exception):
            self._pm.shutdown_all_for_label("exec_job:")


# Process-local singleton + public hooks: single ExecJobRegistry shared by
# tool handlers and SSE transport. Holder is a 1-element list for in-place re-pointing.
_DEFAULT_REGISTRY_HOLDER: list[ExecJobRegistry | None] = [None]
_DEFAULT_REGISTRY_LOCK: Final[threading.Lock] = threading.Lock()


def get_exec_job_registry(
    *,
    process_manager: ProcessManager | None = None,
    clock: Callable[[], float] | None = None,
    liveness_config: LivenessConfig | None = None,
    psutil_module: _PsutilModuleLike | None = None,
    sample_interval_s: float = td.EXEC_LIVENESS_SAMPLE_INTERVAL_S,
    spill_dir: Path | None = None,
) -> ExecJobRegistry:
    """Return the process-local ``ExecJobRegistry`` singleton.

    Lazy-constructs the registry on first access so an importer that never
    dispatches an exec request never pays for it. ``process_manager``,
    ``clock``, ``liveness_config``, ``psutil_module``, ``sample_interval_s``,
    and ``spill_dir`` are honoured only on the FIRST construction; later
    calls return the existing instance regardless of the values supplied, so
    test code that wants a custom registry must call
    :func:`set_exec_job_registry` first.

    Use :func:`reset_exec_job_registry` (or :func:`set_exec_job_registry`)
    to swap in a test double or to clear the singleton between tests.
    """
    if _DEFAULT_REGISTRY_HOLDER[0] is None:
        with _DEFAULT_REGISTRY_LOCK:
            if _DEFAULT_REGISTRY_HOLDER[0] is None:
                _DEFAULT_REGISTRY_HOLDER[0] = ExecJobRegistry(
                    process_manager=process_manager,
                    clock=clock,
                    liveness_config=liveness_config,
                    psutil_module=psutil_module,
                    sample_interval_s=sample_interval_s,
                    spill_dir=spill_dir,
                )
    reg = _DEFAULT_REGISTRY_HOLDER[0]
    if reg is None:
        # Defensive: the holder is mutated only under _DEFAULT_REGISTRY_LOCK,
        # and the double-checked locking above guarantees the slot is filled
        # before this branch is reachable. If a future edit accidentally
        # clears the slot from another thread without holding the lock, fail
        # loudly rather than silently returning None to a caller that typed
        # the result as ``ExecJobRegistry``.
        raise RuntimeError("ExecJobRegistry singleton cleared without lock")
    return reg


def set_exec_job_registry(registry: ExecJobRegistry | None) -> None:
    """Inject or clear the singleton (test seam).

    Pass ``registry=None`` to clear; pass an instance to install it as the
    singleton. Production callers should not use this — use
    :func:`get_exec_job_registry` to obtain the lazily constructed default.
    """
    with _DEFAULT_REGISTRY_LOCK:
        _DEFAULT_REGISTRY_HOLDER[0] = registry


def reset_exec_job_registry() -> None:
    """Clear the singleton (test seam).

    Convenience wrapper equivalent to ``set_exec_job_registry(None)``. The
    next :func:`get_exec_job_registry` call reconstructs with default
    parameters.
    """
    set_exec_job_registry(None)


def attach_progress_subscription(
    registry: ExecJobRegistry,
    *,
    request_id: str,
    progress_token: str | int,
    sink: Callable[[dict[str, object]], None],
    job_id: str,
) -> ProgressSubscription:
    """Attach a request-scoped progress subscription to a specific running job.

    Public hook for the WU-2 worker (``exec`` / ``unsafe_exec`` /
    ``raw_exec`` handlers). The flow is:

    1. The SSE streaming path installs a request-scoped subscription on the
       shared registry as soon as ``_meta.progressToken`` is observed on the
       request (no ``job_id`` yet, since the job hasn't started).
    2. The exec handler calls ``registry.start(...)`` to spawn the job and
       obtain the new ``job_id``.
    3. The exec handler calls ``attach_progress_subscription`` with the
       ``job_id`` and a sink that wraps its ``write_frame`` callable. The
       subscription is now bound to the actual job; subsequent liveness
       steps emit ``notifications/progress`` frames to the sink.

    Bindings are EXPLICIT: the registry dispatch matches subs by exact
    ``job_id`` only, so a subscription whose ``job_id`` differs from the
    sampled job never receives its frames. There is no wildcard/None-means-any
    fallback under concurrency.

    Args:
        registry: The shared ``ExecJobRegistry`` (obtain via
            :func:`get_exec_job_registry`).
        request_id: Stable identifier for this request. Used as the
            subscription's key — re-attaching with the same id replaces the
            prior binding, and ``registry.unsubscribe_progress(request_id)``
            detaches it cleanly at request completion.
        progress_token: The token the SSE streaming path extracted from
            ``_meta.progressToken`` (or ``progressToken``); carried in the
            ``progressToken`` field of emitted ``notifications/progress``
            frames verbatim.
        sink: Callable invoked with a liveness-sample ``dict`` for each
            ``step_job`` dispatch on the bound job. The sink must be
            non-blocking or its blocking time will stall the runner thread.
        job_id: The exact ``job_id`` returned by ``registry.start(...)``.
            Passing a ``job_id`` that does not match a running job leaves
            the subscription installed but inert (no frames emit until a
            job with that id is registered).

    Returns:
        The registered :class:`ProgressSubscription`. The caller can ignore
        the return value if it has no further need — request-scoped
        teardown is driven by ``registry.unsubscribe_progress(request_id)``
        at the end of the request.
    """
    sub = ProgressSubscription(
        request_id=request_id,
        progress_token=str(progress_token),
        sink=sink,
        job_id=job_id,
    )
    registry.subscribe_progress(sub)
    return sub


__all__ = [
    "ExecJobError",
    "ExecJobRegistry",
    "JobResult",
    "JobSnapshot",
    "KillCause",
    "ProgressSubscription",
    "attach_progress_subscription",
    "get_exec_job_registry",
    "reset_exec_job_registry",
    "serialize_liveness_verdict",
    "set_exec_job_registry",
]
