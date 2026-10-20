"""Exec job registry and runner for long-running MCP commands.

Manages long-running exec jobs with fuzzy liveness monitoring, bounded
resource consumption, request-scoped progress subscriptions, and child-owned
shutdown seams.
"""

from __future__ import annotations

import collections
import contextlib
import io
import logging
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, Literal

import psutil

import ralph.timeout_defaults as td
from ralph.mcp.server.runtime import (
    register_owner_loss_hook as _runtime_register_owner_loss_hook,
)
from ralph.mcp.server.runtime import (
    register_shutdown_hook as _runtime_register_shutdown_hook,
)
from ralph.mcp.tools._exec_output_spill import (
    INLINE_OUTPUT_LIMIT_BYTES,
    build_spill_preview,
    spill_output,
)
from ralph.process.manager import ProcessManager, SpawnOptions, get_process_manager
from ralph.process.manager._liveness_assessor import (
    LivenessConfig,
    LivenessVerdict,
    StallIntegrator,
    assess,
    default_liveness_config,
)
from ralph.process.manager._liveness_sampler import TreeSampler, _LivenessProc, _PsutilModuleLike

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ralph.process.manager._managed_process import ManagedProcess

logger = logging.getLogger(__name__)

KillCause = Literal[
    "stalled", "no_progress", "hard_cap", "abandoned", "session_end", "restart_lost"
]
_MAX_UNTERMINATED_LINE_BYTES = 1000


class _DefaultPsutilModule:
    """Default psutil module wrapper satisfying _PsutilModuleLike."""

    def __init__(self) -> None:
        self.Process: Callable[[int], _LivenessProc] = self._get_process

    def _get_process(self, pid: int) -> _LivenessProc:
        return psutil.Process(pid)


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


def _append_tail_buffer(
    head: bytearray, tail: bytearray, chunk: bytes, max_bytes: int = 65_536
) -> None:
    """Retain bounded head and tail buffers of raw bytes."""
    head_capacity = max(0, max_bytes - len(head))
    if head_capacity > 0:
        head.extend(chunk[:head_capacity])
    tail.extend(chunk)
    if len(tail) > max_bytes:
        del tail[:-max_bytes]


def _format_job_output(
    stdout_bytes: bytes,
    stderr_bytes: bytes,
    *,
    total_stdout_len: int,
    total_stderr_len: int,
    spill_dir: Path | None = None,
) -> dict[str, object]:
    """Format captured job output according to standard exec preview/spill shape."""
    stdout_text = stdout_bytes.decode("utf-8", errors="replace")
    stderr_text = stderr_bytes.decode("utf-8", errors="replace")
    combined_len = total_stdout_len + total_stderr_len
    truncated = (total_stdout_len > len(stdout_bytes)) or (total_stderr_len > len(stderr_bytes))

    preview_text = ""
    spill_path: str | None = None

    if combined_len > INLINE_OUTPUT_LIMIT_BYTES or truncated:
        combined_text = (
            f"Stdout:\n{stdout_text}\nStderr:\n{stderr_text}" if stderr_text else stdout_text
        )
        path = spill_output(combined_text, spill_dir)
        spill_path = str(path)
        preview_text = build_spill_preview(combined_text, path, combined_len, truncated=truncated)
    else:
        preview_parts: list[str] = []
        if stdout_text:
            preview_parts.append(f"Stdout:\n{stdout_text}")
        if stderr_text:
            preview_parts.append(f"Stderr:\n{stderr_text}")
        preview_text = "\n\n".join(preview_parts)

    return {
        "stdout": stdout_text,
        "stderr": stderr_text,
        "stdout_bytes": total_stdout_len,
        "stderr_bytes": total_stderr_len,
        "truncated": truncated,
        "spill_path": spill_path,
        "preview": preview_text,
    }


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
        self.last_output_at: float | None = self.start_time
        self.suspicious_started_at: float | None = None

        self.lock = threading.Lock()
        self.done_event = threading.Event()

        self.handle: ManagedProcess | None = None
        self.running = True
        self.cancelled = False
        self.kill_cause: KillCause | None = None
        self.returncode: int | None = None
        self.latest_verdict: LivenessVerdict | None = None

        self.stdout_head = bytearray()
        self.stdout_tail = bytearray()
        self.total_stdout_bytes = 0

        self.stderr_head = bytearray()
        self.stderr_tail = bytearray()
        self.total_stderr_bytes = 0

        self.output_lines: collections.deque[str] = collections.deque(maxlen=1000)
        self.stall_integrator = StallIntegrator(theta=liveness_config.stall_theta)
        self.tree_sampler = TreeSampler(psutil_module, clock, liveness_config)
        self.runner_thread: threading.Thread | None = None


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
        self._owner_loss_hooks: list[
            Callable[[], None]
        ] = []  # bounded-accumulator-ok: registration hooks

        if auto_register_runtime_hooks:
            _runtime_register_shutdown_hook(lambda: self.shutdown_all("session_end"))
            _runtime_register_owner_loss_hook(lambda: self.shutdown_all("session_end"))

    def register_owner_loss_hook(self, hook: Callable[[], None]) -> None:
        """Register an owner-loss hook with the registry and runtime."""
        with self._lock:
            if hook not in self._owner_loss_hooks:
                self._owner_loss_hooks.append(hook)
        with contextlib.suppress(Exception):
            _runtime_register_owner_loss_hook(hook)

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
            stdout_bytes = bytes(job.stdout_head if len(job.stdout_tail) == 0 else job.stdout_tail)
            stderr_bytes = bytes(job.stderr_head if len(job.stderr_tail) == 0 else job.stderr_tail)
            total_stdout = job.total_stdout_bytes
            total_stderr = job.total_stderr_bytes

        return _format_job_output(
            stdout_bytes,
            stderr_bytes,
            total_stdout_len=total_stdout,
            total_stderr_len=total_stderr,
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
            with contextlib.suppress(Exception):
                self._pm.cleanup_orphans(job.handle)
            if job.handle.stdout is not None:
                with contextlib.suppress(Exception):
                    job.handle.stdout.close()
            if job.handle.stderr is not None:
                with contextlib.suppress(Exception):
                    job.handle.stderr.close()

        job.done_event.set()
        now = self._clock()
        result = JobResult(
            job_id=job.job_id,
            returncode=job.returncode,
            output=self._render_job_output(job),
            kill_cause=cause,
            verdict=job.latest_verdict,
            elapsed_s=max(0.0, now - job.start_time),
            cancelled=job.cancelled,
        )

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

    def _mark_finished(self, job: _ActiveJob, returncode: int) -> None:
        """Record normal process exit and reap orphaned descendants."""
        with self._lock:
            if not job.running:
                return
            job.running = False
            job.returncode = returncode
            job.kill_cause = None

        if job.handle is not None:
            with contextlib.suppress(Exception):
                self._pm.cleanup_orphans(job.handle)
            if job.handle.stdout is not None:
                with contextlib.suppress(Exception):
                    job.handle.stdout.close()
            if job.handle.stderr is not None:
                with contextlib.suppress(Exception):
                    job.handle.stderr.close()

        job.done_event.set()
        now = self._clock()
        result = JobResult(
            job_id=job.job_id,
            returncode=returncode,
            output=self._render_job_output(job),
            kill_cause=None,
            verdict=job.latest_verdict,
            elapsed_s=max(0.0, now - job.start_time),
            cancelled=job.cancelled,
        )

        with self._lock:
            self._running_jobs.pop(job.job_id, None)
            self._finished_results.append(result)
            self._finished_capabilities[job.job_id] = job.capability
            valid_ids = {r.job_id for r in self._finished_results}
            self._finished_capabilities = {
                k: v for k, v in self._finished_capabilities.items() if k in valid_ids
            }

        self._teardown_progress_for_job(job.job_id)

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
        if job.max_runtime_ms is not None:
            ceiling_s = min(job.max_runtime_ms / 1000.0, td.EXEC_JOB_CEILING_MAX_S)
        else:
            ceiling_s = td.EXEC_JOB_CEILING_S
        if now - job.start_time >= ceiling_s:
            self._kill_job(job, cause="hard_cap")
            return

        # 4. Sample liveness
        pid = job.handle.pid if job.handle is not None else 0
        with job.lock:
            lines = list(job.output_lines)
            last_out = job.last_output_at

        window = job.tree_sampler.sample(pid, last_out, lines)
        verdict = assess(window, job.liveness_config)
        job.latest_verdict = verdict

        # 5. Stall check
        job.stall_integrator.step(verdict.score, step_dt)
        stall_window_s = job.timeout_ms / 1000.0
        if job.stall_integrator.should_kill(stall_window_s):
            self._kill_job(job, cause="stalled")
            return

        # 6. No progress check
        if verdict.suspicious:
            if job.suspicious_started_at is None:
                job.suspicious_started_at = now
            elif now - job.suspicious_started_at >= td.EXEC_NO_PROGRESS_WINDOW_S:
                self._kill_job(job, cause="no_progress")
                return
        else:
            job.suspicious_started_at = None

        # 7. Progress notifications
        self._dispatch_progress_for_job(job)

    def _read_stream(self, job: _ActiveJob, stream: IO[bytes] | None, is_stdout: bool) -> None:
        """Reader thread helper draining stdout/stderr chunks."""
        if stream is None:
            return
        buffer_line = bytearray()
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
                        job.output_lines.append(decoded)
                if len(buffer_line) > _MAX_UNTERMINATED_LINE_BYTES:
                    decoded = buffer_line.decode("utf-8", errors="replace").strip()
                    job.output_lines.append(decoded)
                    buffer_line = bytearray()

    def _runner_loop(self, job: _ActiveJob) -> None:
        """Periodic background runner loop for active job."""
        while not job.done_event.wait(timeout=self._sample_interval_s):
            if job.done_event.is_set():
                break
            self.step_job(job.job_id)

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
        with self._lock:
            if len(self._running_jobs) >= td.EXEC_MAX_RUNNING_JOBS:
                raise ExecJobError(
                    "", f"Running job limit exceeded (max {td.EXEC_MAX_RUNNING_JOBS})"
                )
            job_id = f"job_{secrets.token_urlsafe(12)}"

        if progress is not None:
            progress.job_id = job_id
            self.subscribe_progress(progress)

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
            self._teardown_progress_for_job(job_id)
            raise ExecJobError(job_id, f"Failed to spawn job: {exc}") from exc

        # Spawn stdout and stderr reader threads
        t_stdout = threading.Thread(
            target=self._read_stream,
            args=(active, handle.stdout, True),
            name=f"job-stdout-{job_id}",
            daemon=True,
        )
        t_stderr = threading.Thread(
            target=self._read_stream,
            args=(active, handle.stderr, False),
            name=f"job-stderr-{job_id}",
            daemon=True,
        )
        t_stdout.start()
        t_stderr.start()

        with self._lock:
            self._running_jobs[job_id] = active

        if self._background_sampler:
            runner = threading.Thread(
                target=self._runner_loop,
                args=(active,),
                name=f"job-runner-{job_id}",
                daemon=True,
            )
            active.runner_thread = runner
            runner.start()

        return job_id

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
            active = self._running_jobs.get(job_id)
            if active is None:
                # Check finished results
                for res in reversed(self._finished_results):
                    if res.job_id == job_id:
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

            if active.capability != capability:
                raise ExecJobError(
                    job_id,
                    f"capability mismatch: expected {active.capability}, got {capability}",
                )

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
                    now = self._clock()
                    return JobResult(
                        job_id=active.job_id,
                        returncode=active.returncode,
                        output=self._render_job_output(active),
                        kill_cause=active.kill_cause,
                        verdict=active.latest_verdict,
                        elapsed_s=max(0.0, now - active.start_time),
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
            if progress is not None:
                self.unsubscribe_progress(progress.request_id)

    def cancel(self, job_id: str, *, capability: str) -> JobResult:
        """Explicitly cancel a running or completed job."""
        with self._lock:
            active = self._running_jobs.get(job_id)
            if active is None:
                for res in reversed(self._finished_results):
                    if res.job_id == job_id:
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

            if active.capability != capability:
                raise ExecJobError(
                    job_id,
                    f"capability mismatch: expected {active.capability}, got {capability}",
                )

            active.cancelled = True

        self._kill_job(active, cause=None)

        with self._lock:
            for res in reversed(self._finished_results):
                if res.job_id == job_id:
                    return res
        now = self._clock()
        return JobResult(
            job_id=active.job_id,
            returncode=None,
            output=self._render_job_output(active),
            kill_cause=None,
            verdict=active.latest_verdict,
            elapsed_s=max(0.0, now - active.start_time),
            cancelled=True,
        )

    def shutdown_all(self, cause: KillCause = "session_end") -> None:
        """Forcibly kill all active running jobs."""
        with self._lock:
            active_list = list(self._running_jobs.values())

        for active in active_list:
            self._kill_job(active, cause=cause)

        # Defense-in-depth: reap any lingering processes under label prefix
        with contextlib.suppress(Exception):
            self._pm.shutdown_all_for_label("exec_job:")


__all__ = [
    "ExecJobError",
    "ExecJobRegistry",
    "JobResult",
    "JobSnapshot",
    "KillCause",
    "ProgressSubscription",
]
