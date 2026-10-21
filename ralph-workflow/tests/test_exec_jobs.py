"""Unit and lifecycle tests for ExecJobRegistry (§3b / WU-3).

All tests use deterministic fake clocks and mock process managers — zero wall-clock
sleeps, zero real subprocesses, fully typed with no type: ignore markers.
"""

from __future__ import annotations

import contextlib
import itertools
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

import pytest

from ralph.mcp.tools._exec_jobs import (
    ExecJobError,
    ExecJobRegistry,
    JobResult,
    JobSnapshot,
    ProgressSubscription,
)
from ralph.process.manager import (
    ManagedAsyncProcess,
    ManagedProcess,
    ManagedPtyProcess,
    ProcessManager,
    ProcessManagerPolicy,
    SpawnOptions,
)
from ralph.process.manager._liveness_assessor import LivenessConfig
from ralph.testing._fake_popen import FakePopen
from ralph.testing._fake_psutil import FakePsutil
from ralph.testing._process_state import ProcessState
from ralph.timeout_defaults import (
    EXEC_JOB_ABANDON_S,
    EXEC_JOB_RESULT_FIFO,
    EXEC_MAX_RUNNING_JOBS,
)

if TYPE_CHECKING:
    from ralph.process.manager._liveness_sampler import LivenessProc
    from ralph.process.manager._process_manager_types import (
        _PsutilModuleLike,
        _SyncProcessFactory,
        _SyncProcessLike,
    )


class _FakeClock:
    """Deterministic fake clock for lifecycle tests."""

    def __init__(self, initial_time: float = 1000.0) -> None:
        self._time = initial_time

    def __call__(self) -> float:
        return self._time

    def advance(self, dt: float) -> None:
        self._time += dt


FakeClock = _FakeClock


class _CpuTimesNamedTuple(NamedTuple):
    user: float
    system: float


CpuTimesNamedTuple = _CpuTimesNamedTuple


class _IoCountersNamedTuple(NamedTuple):
    read_bytes: int
    write_bytes: int


IoCountersNamedTuple = _IoCountersNamedTuple


class _CtxSwitchesNamedTuple(NamedTuple):
    voluntary: int
    involuntary: int = 0


CtxSwitchesNamedTuple = _CtxSwitchesNamedTuple


@dataclass
class _FakeSamplerProc:
    """Mock process for TreeSampler."""

    pid: int
    user_cpu: float = 0.0
    system_cpu: float = 0.0
    read_bytes: int = 0
    write_bytes: int = 0
    voluntary_ctx: int = 0
    creation_time: float = 0.0
    child_procs: list[_FakeSamplerProc] = field(default_factory=list)

    def children(self, recursive: bool = False) -> Sequence[LivenessProc]:
        if not recursive:
            return list(self.child_procs)
        all_procs: list[LivenessProc] = []
        for c in self.child_procs:
            all_procs.append(c)
            all_procs.extend(c.children(recursive=True))
        return all_procs

    def cpu_times(self) -> _CpuTimesNamedTuple:
        return _CpuTimesNamedTuple(user=self.user_cpu, system=self.system_cpu)

    def io_counters(self) -> _IoCountersNamedTuple:
        return _IoCountersNamedTuple(read_bytes=self.read_bytes, write_bytes=self.write_bytes)

    def num_ctx_switches(self) -> _CtxSwitchesNamedTuple:
        return _CtxSwitchesNamedTuple(voluntary=self.voluntary_ctx)

    def create_time(self) -> float:
        return self.creation_time


FakeSamplerProc = _FakeSamplerProc


class _FakePsutilModule:
    """Mock psutil module for TreeSampler."""

    Process: Callable[[int], LivenessProc]

    def __init__(self, processes: dict[int, _FakeSamplerProc] | None = None) -> None:
        self.procs: dict[int, _FakeSamplerProc] = processes if processes is not None else {}
        self.Process = self._get_process

    def _get_process(self, pid: int) -> LivenessProc:
        if pid not in self.procs:
            return _FakeSamplerProc(pid=pid)
        return self.procs[pid]


FakePsutilModule = _FakePsutilModule


def _make_test_config() -> LivenessConfig:
    return LivenessConfig(
        output_recent_min_s=10.0,
        output_recent_max_s=60.0,
        cpu_busy_min=0.02,
        cpu_busy_max=0.20,
        io_kbps_inactive=4.0,
        io_kbps_active=64.0,
        tree_active_threshold=1,
        repetitive_max=0.1,
        repetitive_min=0.5,
        awake_per_s_inactive=0.1,
        awake_per_s_active=2.0,
        stall_theta=0.5,
        evidence_window_s=30.0,
    )


class _TrackingProcessManager(ProcessManager):
    """ProcessManager subclass that tracks orphan cleanup."""

    def __init__(
        self,
        policy: ProcessManagerPolicy | None = None,
        sync_process_factory: _SyncProcessFactory | None = None,
        psutil: _PsutilModuleLike | None = None,
    ) -> None:
        super().__init__(policy=policy, sync_process_factory=sync_process_factory, psutil=psutil)
        self.orphans_cleaned: list[int] = []

    def cleanup_orphans(
        self,
        handle_or_pid: ManagedProcess | ManagedPtyProcess | ManagedAsyncProcess | int,
        *,
        label_prefix: str | None = None,
    ) -> None:
        raw_pid = handle_or_pid if isinstance(handle_or_pid, int) else handle_or_pid.pid
        self.orphans_cleaned.append(raw_pid)
        super().cleanup_orphans(handle_or_pid, label_prefix=label_prefix)


TrackingProcessManager = _TrackingProcessManager


class _TestEnv:
    """Bundle containing FakeClock, ProcessManager, and ExecJobRegistry."""

    __test__ = False

    def __init__(self) -> None:
        self.clock = FakeClock(1000.0)
        self.pids = itertools.count(100)
        self.procs: dict[int, FakePopen] = {}
        self.sampler_procs: dict[int, FakeSamplerProc] = {}
        self.psutil_module = FakePsutilModule(self.sampler_procs)

        def sync_factory(command: Sequence[str], opts: SpawnOptions) -> _SyncProcessLike:
            pid = next(self.pids)
            p = FakePopen(pid=pid, state=ProcessState(returncode=None))
            self.procs[pid] = p
            self.sampler_procs[pid] = FakeSamplerProc(pid=pid)
            return p

        self.pm = TrackingProcessManager(
            policy=ProcessManagerPolicy(default_grace_period_s=0.0),
            sync_process_factory=sync_factory,
            psutil=FakePsutil(),
        )

        self.registry = ExecJobRegistry(
            process_manager=self.pm,
            clock=self.clock,
            liveness_config=_make_test_config(),
            psutil_module=self.psutil_module,
            auto_register_runtime_hooks=False,
            background_sampler=False,
        )


# ---------------------------------------------------------------------------
# Basic lifecycle tests
# ---------------------------------------------------------------------------


def test_job_finish_in_budget() -> None:
    env = _TestEnv()
    job_id = env.registry.start(["echo", "hello"], capability="ProcessExecBounded")

    active_proc = next(iter(env.procs.values()))
    active_proc._returncode = 0

    env.registry.step_job(job_id)
    result = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")

    assert isinstance(result, JobResult)
    assert result.job_id == job_id
    assert result.returncode == 0
    assert result.kill_cause is None
    assert not result.cancelled


def test_job_snapshot_then_final() -> None:
    env = _TestEnv()
    job_id = env.registry.start(["long_running"], capability="ProcessExecBounded")

    # While running, wait returns JobSnapshot
    snapshot = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(snapshot, JobSnapshot)
    assert snapshot.job_id == job_id
    assert snapshot.running
    assert snapshot.instruction == "call exec_wait; do not re-run"

    # Finish process
    active_proc = next(iter(env.procs.values()))
    active_proc._returncode = 0
    env.registry.step_job(job_id)

    # Next wait returns final JobResult
    final = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(final, JobResult)
    assert final.job_id == job_id
    assert final.returncode == 0
    assert final.kill_cause is None


def test_job_cancel() -> None:
    env = _TestEnv()
    job_id = env.registry.start(["long_running"], capability="ProcessExecBounded")
    active_proc = next(iter(env.procs.values()))

    result = env.registry.cancel(job_id, capability="ProcessExecBounded")
    assert isinstance(result, JobResult)
    assert result.job_id == job_id
    assert result.cancelled
    assert result.kill_cause is None
    assert active_proc._terminated


def test_job_abandonment() -> None:
    env = _TestEnv()
    job_id = env.registry.start(["long_running"], capability="ProcessExecBounded")
    active_proc = next(iter(env.procs.values()))

    # Advance clock past abandon window without calling wait
    env.clock.advance(EXEC_JOB_ABANDON_S + 1.0)
    env.registry.step_job(job_id)

    result = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(result, JobResult)
    assert result.kill_cause == "abandoned"
    assert active_proc._terminated


def test_job_stalled() -> None:
    env = _TestEnv()
    # Configure fast output aging so stall integrator accumulates pressure
    fast_config = LivenessConfig(
        output_recent_min_s=1.0,
        output_recent_max_s=2.0,
        cpu_busy_min=0.02,
        cpu_busy_max=0.20,
        io_kbps_inactive=4.0,
        io_kbps_active=64.0,
        tree_active_threshold=1,
        repetitive_max=0.1,
        repetitive_min=0.5,
        awake_per_s_inactive=0.1,
        awake_per_s_active=2.0,
        stall_theta=0.5,
        evidence_window_s=5.0,
    )
    env.registry._liveness_config = fast_config

    # 5s stall window
    job_id = env.registry.start(["idle_command"], timeout_ms=5_000, capability="ProcessExecBounded")
    active_proc = next(iter(env.procs.values()))

    # Keep wait refreshed while stepping through stall window
    for _ in range(10):
        env.clock.advance(1.0)
        env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        env.registry.step_job(job_id, dt=1.0)

    result = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(result, JobResult)
    assert result.kill_cause == "stalled"
    assert active_proc._terminated


def test_job_no_progress() -> None:
    env = _TestEnv()
    job_id = env.registry.start(["spin"], capability="ProcessExecBounded")
    active_proc = next(iter(env.procs.values()))
    sampler_proc = env.sampler_procs[active_proc.pid]

    # Advance beyond 900s of continuous suspicion in 10s increments with high CPU usage (0.8) and wait refreshed
    for _ in range(105):
        env.clock.advance(10.0)
        sampler_proc.user_cpu += 8.0
        env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        env.registry.step_job(job_id, dt=10.0)

    result = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(result, JobResult)
    assert result.kill_cause == "no_progress"
    assert active_proc._terminated


def test_job_shutdown_all() -> None:
    env = _TestEnv()
    j1 = env.registry.start(["cmd1"], capability="ProcessExecBounded")
    j2 = env.registry.start(["cmd2"], capability="ProcessExecBounded")

    env.registry.shutdown_all("session_end")

    res1 = env.registry.wait(j1, wait_ms=0, capability="ProcessExecBounded")
    res2 = env.registry.wait(j2, wait_ms=0, capability="ProcessExecBounded")

    assert isinstance(res1, JobResult)
    assert res1.kill_cause == "session_end"
    assert isinstance(res2, JobResult)
    assert res2.kill_cause == "session_end"


# ---------------------------------------------------------------------------
# Gating and Error Handling Tests
# ---------------------------------------------------------------------------


def test_job_unknown_job_id() -> None:
    env = _TestEnv()
    try:
        env.registry.wait("unknown_123", wait_ms=0, capability="ProcessExecBounded")
        raise AssertionError("Expected ExecJobError")
    except ExecJobError as exc:
        assert "unknown or expired job" in str(exc)

    try:
        env.registry.cancel("unknown_123", capability="ProcessExecBounded")
        raise AssertionError("Expected ExecJobError")
    except ExecJobError as exc:
        assert "unknown or expired job" in str(exc)


def test_capability_gating() -> None:
    env = _TestEnv()
    job_id = env.registry.start(["cmd"], capability="ProcessExecBounded")

    try:
        env.registry.wait(job_id, wait_ms=0, capability="OtherCapability")
        raise AssertionError("Expected ExecJobError")
    except ExecJobError as exc:
        assert "capability mismatch" in str(exc)

    try:
        env.registry.cancel(job_id, capability="OtherCapability")
        raise AssertionError("Expected ExecJobError")
    except ExecJobError as exc:
        assert "capability mismatch" in str(exc)


def test_running_jobs_cap() -> None:
    env = _TestEnv()
    jobs: list[str] = []
    for _ in range(EXEC_MAX_RUNNING_JOBS):
        jobs.append(env.registry.start(["sleep"], capability="ProcessExecBounded"))

    try:
        env.registry.start(["sleep_5th"], capability="ProcessExecBounded")
        raise AssertionError("Expected ExecJobError")
    except ExecJobError as exc:
        assert "Running job limit exceeded" in str(exc)

    # Cancel one job to free a slot
    env.registry.cancel(jobs[0], capability="ProcessExecBounded")
    # Now starting a 5th job succeeds
    j5 = env.registry.start(["sleep_5th"], capability="ProcessExecBounded")
    assert j5 is not None


@pytest.mark.timeout_seconds(3.0)
def test_finished_results_fifo_cap() -> None:
    env = _TestEnv()
    jobs: list[str] = []

    # Start and finish 17 jobs sequentially
    for i in range(EXEC_JOB_RESULT_FIFO + 1):
        jid = env.registry.start([f"cmd_{i}"], capability="ProcessExecBounded")
        proc = list(env.procs.values())[-1]
        proc._returncode = 0
        env.registry.step_job(jid)
        jobs.append(jid)

    # Job 0 must be evicted from FIFO (maxlen=16)
    try:
        env.registry.wait(jobs[0], wait_ms=0, capability="ProcessExecBounded")
        raise AssertionError("Expected ExecJobError for evicted job")
    except ExecJobError as exc:
        assert "unknown or expired job" in str(exc)

    # Job 1 through 16 are still present
    for jid in jobs[1:]:
        res = env.registry.wait(jid, wait_ms=0, capability="ProcessExecBounded")
        assert isinstance(res, JobResult)
        assert res.job_id == jid


def test_progress_subscription_lifecycle() -> None:
    env = _TestEnv()
    frames: list[dict[str, object]] = []

    def sink(frame: dict[str, object]) -> None:
        frames.append(frame)

    sub = ProgressSubscription(request_id="req-1", progress_token="tok-abc", sink=sink)
    job_id = env.registry.start(["cmd"], capability="ProcessExecBounded", progress=sub)

    # Step job dispatches a progress frame
    env.clock.advance(2.0)
    env.registry.step_job(job_id, dt=2.0)

    assert len(frames) == 1
    assert frames[0]["progressToken"] == "tok-abc"
    assert frames[0]["job_id"] == job_id

    # Wait with a different subscription
    wait_frames: list[dict[str, object]] = []
    wait_sub = ProgressSubscription(
        request_id="req-2",
        progress_token="tok-wait",
        sink=wait_frames.append,
        job_id=job_id,
    )

    env.registry.subscribe_progress(wait_sub)
    env.clock.advance(2.0)
    env.registry.step_job(job_id, dt=2.0)

    assert len(frames) == 2
    assert len(wait_frames) == 1
    assert wait_frames[0]["progressToken"] == "tok-wait"

    # Finish job -> subscriptions torn down
    active_proc = next(iter(env.procs.values()))
    active_proc._returncode = 0
    env.registry.step_job(job_id)

    # Subsequent steps do NOT dispatch frames to torn-down sinks
    env.clock.advance(2.0)
    env.registry.step_job(job_id, dt=2.0)
    assert len(frames) == 2
    assert len(wait_frames) == 1


# ---------------------------------------------------------------------------
# The 4 Required Lifecycle Traces
# ---------------------------------------------------------------------------


def test_trace_1_root_exit_with_orphaned_child() -> None:
    """Trace 1: root exits, orphan keeps pipes open -> job finishes, tree cleaned."""
    env = _TestEnv()
    job_id = env.registry.start(["sh", "-c", "sleep 100 &"], capability="ProcessExecBounded")
    root_proc = next(iter(env.procs.values()))

    # Root process exits with 0
    root_proc._returncode = 0

    # Step job detects root exit immediately
    env.registry.step_job(job_id)

    result = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(result, JobResult)
    assert result.returncode == 0
    assert result.kill_cause is None
    # Orphan cleanup verified
    assert root_proc.pid in env.pm.orphans_cleaned


def test_trace_2_hard_cap_at_ceiling() -> None:
    """Trace 2: job outlives 60-min default ceiling -> killed with hard_cap."""
    env = _TestEnv()
    job_id = env.registry.start(["long_build"], capability="ProcessExecBounded")
    root_proc = next(iter(env.procs.values()))
    active_job = env.registry._running_jobs[job_id]

    # Advance clock to 3601s (> 3600s EXEC_JOB_CEILING_S) with active output
    for _ in range(121):
        env.clock.advance(30.0)
        active_job.last_output_at = env.clock()
        env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        env.registry.step_job(job_id, dt=30.0)

    result = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(result, JobResult)
    assert result.kill_cause == "hard_cap"
    assert root_proc._terminated


# ---------------------------------------------------------------------------
# G2 randomized bounded property — every HEALTHY trace ends at the ceiling.
#
# PRODUCT_CRITERIA Verification §3 "Bounded": randomized property proof
# driving the REAL ``ExecJobRegistry.step_job`` through the production
# hard-cap branch at ``_exec_jobs.py:503-505`` (complementary to the scripted
# regression above; NOT a replacement).
#
# HEALTHY trace (cpu_ratio ~0.5, 128 KiB/s I/O, 20 ctx/s, fresh output) keeps
# abandonment, stall, suspicion-clock branches unreachable; ceilings stay
# well below ``EXEC_JOB_ABANDON_S`` and ``EXEC_NO_PROGRESS_WINDOW_S`` so those
# stay unreachable too.
#
# CLEANUP-EVIDENCE SCOPE: root termination + ``ProcessManager`` cleanup
# invocation only — NOT descendant-tree reaping (the ``_FakeSamplerProc`` and
# ``TrackingProcessManager.cleanup_orphans`` graphs cannot jointly observe
# it). Real descendant cleanup is proven by
# ``tests/test_exec_job_shutdown_subprocess_e2e.py``.
# ---------------------------------------------------------------------------


def test_randomized_hard_cap_terminates_healthily_within_budget_at_real_registry_branch() -> None:
    """Randomized Bounded property: HEALTHY jobs end at the real hard-cap branch.

    Per case: ``kill_cause == 'hard_cap'``; ``elapsed_s`` lies in
    ``[ceiling, ceiling + sample_interval]``; mid-trace ``JobSnapshot`` proves
    the job was still running, classified ``working`` with zero stall pressure;
    root terminated and ``ProcessManager`` cleanup invoked (root-only scope).
    """
    import math
    import random

    from ralph import timeout_defaults as _td

    # Float tolerance for the fake clock's repeated ``+=``; not a behavioural
    # margin — a kill one whole sample interval early still fails by a wide gap.
    timing_eps = 1e-9
    rng = random.Random(20260920)

    for case_idx in range(8):
        ceiling_ms = rng.randrange(10_000, 60_001, 500)
        sample_interval_s = round(rng.uniform(1.0, 2.0), 3)
        effective_ceiling_s = ceiling_ms / 1000.0
        max_steps = math.ceil(effective_ceiling_s / sample_interval_s) + 2
        precondition_step = max_steps // 2
        case_log: list[str] = [
            f"case={case_idx} ceiling_ms={ceiling_ms} "
            f"sample_interval_s={sample_interval_s} max_steps={max_steps}"
        ]

        # The corpus must stay inside the window where the hard-cap branch is
        # the only reachable one, or the test would prove nothing.
        assert sample_interval_s <= _td.EXEC_LIVENESS_SAMPLE_INTERVAL_S, "; ".join(case_log)
        assert effective_ceiling_s < _td.EXEC_JOB_ABANDON_S, "; ".join(case_log)
        assert effective_ceiling_s < _td.EXEC_NO_PROGRESS_WINDOW_S, "; ".join(case_log)

        env = _TestEnv()
        job_id = env.registry.start(
            ["healthy_thing"],
            max_runtime_ms=ceiling_ms,
            capability="ProcessExecBounded",
        )
        root_proc = next(iter(env.procs.values()))
        sampler_proc = env.sampler_procs[root_proc.pid]
        active_job = env.registry._running_jobs[job_id]

        mid_snapshot: JobSnapshot | None = None
        killed_at_step = 0

        for step_idx in range(max_steps):
            env.clock.advance(sample_interval_s)
            # Healthy signal knobs, rate-matched to the sampling cadence so the
            # windowed sampler reads cpu_ratio ~0.5, 128 KiB/s and 20 ctx/s.
            sampler_proc.user_cpu += 0.5 * sample_interval_s
            sampler_proc.read_bytes += int(128 * 1024 * sample_interval_s)
            sampler_proc.voluntary_ctx += int(20 * sample_interval_s)
            active_job.last_output_at = env.clock()
            # ``wait`` refreshes ``last_wait_time`` so the abandonment branch
            # cannot fire — this is the real client's poll cadence.
            pending = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
            if step_idx == precondition_step and isinstance(pending, JobSnapshot):
                mid_snapshot = pending
            env.registry.step_job(job_id, dt=sample_interval_s)
            if job_id not in env.registry._running_jobs:
                killed_at_step = step_idx + 1
                break

        case_log.append(f"killed_at_step={killed_at_step}")
        assert killed_at_step > 0, (
            f"healthy job outlived its {ceiling_ms}ms ceiling for {max_steps} samples; "
            + "; ".join(case_log)
        )

        result = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        assert isinstance(result, JobResult), "; ".join(case_log)
        case_log.append(
            f"kill_cause={result.kill_cause} elapsed_s={result.elapsed_s!r} "
            f"returncode={result.returncode} cancelled={result.cancelled} "
            f"ceiling_ms={result.ceiling_ms}"
        )

        assert result.kill_cause == "hard_cap", "; ".join(case_log)
        assert result.cancelled is False, "; ".join(case_log)
        assert result.returncode is None, "; ".join(case_log)
        # The requested ceiling is inside EXEC_JOB_CEILING_MAX_S, so the
        # production clamp must leave it untouched.
        assert result.ceiling_ms == ceiling_ms, "; ".join(case_log)
        assert result.elapsed_s >= effective_ceiling_s - timing_eps, "; ".join(case_log)
        assert result.elapsed_s <= effective_ceiling_s + sample_interval_s + timing_eps, "; ".join(
            case_log
        )

        # HEALTHY PRECONDITION: mid-trace the job was alive, below the ceiling,
        # and classified working by the real assessor (not simulated here).
        # ``is not None`` also narrows the saved reference to ``JobSnapshot``.
        assert mid_snapshot is not None, "; ".join(case_log)
        mid_verdict = mid_snapshot.verdict
        assert mid_verdict is not None, "; ".join(case_log)
        case_log.append(
            f"mid_elapsed_s={mid_snapshot.elapsed_s!r} "
            f"kind={mid_verdict.verdict_kind} rule={mid_verdict.strongest_rule} "
            f"score={mid_verdict.score!r} pressure={mid_verdict.pressure!r} "
            f"signals={mid_verdict.signals}"
        )

        assert mid_snapshot.running is True, "; ".join(case_log)
        assert mid_snapshot.elapsed_s < effective_ceiling_s, "; ".join(case_log)
        assert mid_verdict.verdict_kind == "working", "; ".join(case_log)
        assert mid_verdict.suspicious is False, "; ".join(case_log)
        assert mid_verdict.score > _make_test_config().stall_theta, "; ".join(case_log)
        assert mid_verdict.pressure == 0.0, "; ".join(case_log)

        # CLEANUP EVIDENCE (root-only scope — NOT descendant tree reaping).
        assert root_proc._terminated is True, "; ".join(case_log)
        assert root_proc.pid in env.pm.orphans_cleaned, "; ".join(case_log)


def test_trace_3_three_hour_e2e_completing_via_max_runtime() -> None:
    """Trace 3: 3-h e2e with max_runtime_ms runs past 1h without kill and completes."""
    env = _TestEnv()
    # 4 hours max runtime cap = 14_400_000 ms
    job_id = env.registry.start(
        ["e2e_test"],
        max_runtime_ms=4 * 3600 * 1000,
        capability="ProcessExecBounded",
    )
    root_proc = next(iter(env.procs.values()))
    active_job = env.registry._running_jobs[job_id]

    # Advance clock to 7200s (2 hours) with active output — must NOT be killed by default 1h ceiling!
    for _ in range(240):
        env.clock.advance(30.0)
        active_job.last_output_at = env.clock()
        env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        env.registry.step_job(job_id, dt=30.0)

    # Job still running
    snap = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(snap, JobSnapshot)
    assert snap.running

    # Advance to 3.0 hours (10,800s = additional 3600s) and finish
    for _ in range(120):
        env.clock.advance(30.0)
        active_job.last_output_at = env.clock()
        env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        env.registry.step_job(job_id, dt=30.0)

    root_proc._returncode = 0
    env.registry.step_job(job_id, dt=1.0)

    final = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(final, JobResult)
    assert final.returncode == 0
    assert final.kill_cause is None


def test_trace_4_disconnected_request_scenario() -> None:
    """Trace 4: client request times out / disconnects, job continues and finishes."""
    env = _TestEnv()
    job_id = env.registry.start(["render_video"], capability="ProcessExecBounded")
    root_proc = next(iter(env.procs.values()))

    # Client calls wait with short timeout, receives JobSnapshot
    snap = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(snap, JobSnapshot)

    # Client "disconnects" / abandons HTTP request.
    # Job advances in the background, finishes naturally.
    env.clock.advance(30.0)
    root_proc._returncode = 0
    env.registry.step_job(job_id, dt=30.0)

    # Later, client reconnects and calls wait with the job_id
    reconnect_result = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(reconnect_result, JobResult)
    assert reconnect_result.returncode == 0
    assert reconnect_result.kill_cause is None


# ---------------------------------------------------------------------------
# WU-3 EV-002 regression — default-config idle trace kills at stall_window
# ---------------------------------------------------------------------------
#
# Reproduces the WU-3 Wt-19 EV-002 finding: the default never-output idle
# trace reaches ``should_kill(90)`` at 138 s instead of the requested
# 90 s plus one sample interval. The root cause was
# ``_ActiveJob.last_output_at`` initialised to ``start_time``; the fix
# (this revision) initialises it to ``None`` so the first sample's
# ``seconds_since_output`` is ``None`` → OutputRecent=0 → R5 fires alone
# → score=0 from t=0 → pressure builds at 1 s/s → kill at 90 s ± one
# sample interval. The default ``EXEC_LIVENESS_SAMPLE_INTERVAL_S`` is
# 2 s, so the regression asserts the kill lands in the
# 88 s..92 s window (90 s ± one interval, inclusive of any rounding
# induced by the fake clock advancing in 1 s ticks).


def test_default_idle_kills_at_90s() -> None:
    """Default-config idle trace kills at 90 s ± one sample interval.

    Drives ``step_job`` with ``EXEC_LIVENESS_SAMPLE_INTERVAL_S`` (2 s)
    ticks for a job whose command emits no output (zero CPU, zero I/O,
    zero children, zero ctx switches). Uses the production
    ``default_liveness_config`` and ``timeout_ms`` = 90_000 (the §3b
    ``EXEC_DEFAULT_TIMEOUT_MS`` default — ``timeout_ms`` is the
    ``stall_window``).

    The fix for the EV-002 bug initialises ``last_output_at=None`` so
    the first sample's ``seconds_since_output`` is ``None`` rather
    than ``0`` (which would have credited fresh-output membership and
    drained pressure for the first ``output_recent_max_s`` seconds).
    """
    from ralph import timeout_defaults as _td
    from ralph.process.manager._liveness_assessor import (
        StallIntegrator,
        default_liveness_config,
    )

    cfg = default_liveness_config()
    clock = FakeClock(1000.0)
    pids = itertools.count(100)
    procs: dict[int, FakePopen] = {}
    sampler_procs: dict[int, FakeSamplerProc] = {}

    def sync_factory(command: Sequence[str], opts: SpawnOptions) -> _SyncProcessLike:
        pid = next(pids)
        p = FakePopen(pid=pid, state=ProcessState(returncode=None))
        procs[pid] = p
        sampler_procs[pid] = FakeSamplerProc(pid=pid)
        return p

    pm = TrackingProcessManager(
        policy=ProcessManagerPolicy(default_grace_period_s=0.0),
        sync_process_factory=sync_factory,
        psutil=FakePsutil(),
    )
    psutil_module = FakePsutilModule(sampler_procs)

    registry = ExecJobRegistry(
        process_manager=pm,
        clock=clock,
        liveness_config=cfg,
        psutil_module=psutil_module,
        auto_register_runtime_hooks=False,
        background_sampler=False,
        sample_interval_s=_td.EXEC_LIVENESS_SAMPLE_INTERVAL_S,
    )

    # Start an idle job with the production 90 s default stall window.
    # ``timeout_ms`` is the §3b ``stall_window``; 90 s is the production
    # ``EXEC_DEFAULT_TIMEOUT_MS`` and the default registered by
    # ``ralph.timeout_defaults``.
    job_id = registry.start(["idle"], timeout_ms=90_000, capability="ProcessExecBounded")
    active = registry._running_jobs[job_id]
    # Pin a fresh StallIntegrator so any prior history from the test
    # scaffold cannot bleed into this regression.
    active.stall_integrator = StallIntegrator(theta=cfg.stall_theta)

    kill_at: float | None = None
    dt = _td.EXEC_LIVENESS_SAMPLE_INTERVAL_S  # 2 s
    # 50 ticks * 2 s = 100 s ceiling; the kill must fire well before.
    for _ in range(50):
        clock.advance(dt)
        registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        registry.step_job(job_id, dt=dt)
        if registry._running_jobs.get(job_id) is None or not active.running:
            kill_at = registry._finished_results[-1].elapsed_s
            break

    assert kill_at is not None, "idle job did not terminate within 100 s"
    # 90 s ± one sample interval (2 s). Strict bounds keep the
    # regression loud if either the integrator re-opens the
    # ``start_time=last_output_at`` credit or drifts further.
    assert 88.0 <= kill_at <= 92.0, (
        f"idle kill fired at {kill_at}s, expected 90s ± 1 sample interval "
        f"(88 s..92 s); the EV-002 bug returned 138 s"
    )
    finished = registry._finished_results[-1]
    assert finished.kill_cause == "stalled"


# ---------------------------------------------------------------------------
# WU-4: Session-end teardown + restart-loss lifecycle coverage
# ---------------------------------------------------------------------------
#
# Session-end path: when ``atexit.register(_run_shutdown_hooks)`` /
# ``install_shutdown_signal_handlers`` fire the registered shutdown hooks at
# process / signal end, every ``ExecJobRegistry`` that opted in via
# ``auto_register_runtime_hooks=True`` runs ``shutdown_all("session_end")``.
# The contract (ralph/mcp/tools/_exec_jobs.py:290 / :291):
#   * every running process tree is killed via ``_kill_job(...)``
#   * the recorded ``kill_cause`` is exactly ``"session_end"``
#   * no job remains waitable as "running" in the same registry afterwards
#
# Restart-loss path: when the MCP server restarts the OS reaps every previous
# process tree. A FRESH ``ExecJobRegistry`` (new ProcessManager, new FIFO)
# has no memory of any prior job. The contract is the only thing observable
# from the new server's side: ``wait(job_id)`` for any non-running,
# non-finishing job_id raises ``ExecJobError`` whose message includes the
# contractual "lost in server restart" wording. The error is non-retryable
# (idempotent raise), never hangs, and never fabricates a JobResult or
# JobSnapshot.
#
# Production gap (see Findings): the declared ``"restart_lost"`` kill cause
# (ralph/mcp/tools/_exec_jobs.py:52) has no production caller — nothing
# records ``kill_cause == "restart_lost"`` on a job whose owning server
# restarted. After a restart the new registry has no memory of old jobs at
# all, so the only observable restart signal is the ExecJobError wording
# from registry.wait().


def test_session_end_via_registered_shutdown_hook_records_session_end_cause() -> None:
    """The session-end shutdown hook kills running jobs with ``kill_cause='session_end'``.

    Registers the session-end hook exactly the way ``ExecJobRegistry.__init__``
    does when ``auto_register_runtime_hooks=True`` (ralph/mcp/tools/_exec_jobs.py:290)
    and invokes it the way ``atexit.register(_run_shutdown_hooks)`` /
    ``install_shutdown_signal_handlers`` do at process / signal end.

    Asserts observable behaviour only:
      * the FakePopen handle's ``_terminated`` flag flips (process-tree death
        observed through the existing fake process seam)
      * the recorded ``JobResult.kill_cause`` is ``"session_end"``
      * the registry's ``_running_jobs`` dict is empty afterwards — no job
        is waitable as "running" in the same registry anymore
    """
    from ralph.mcp.server._runtime_hooks import (
        _SHUTDOWN_HOOKS,
        register_shutdown_hook,
    )

    env = _TestEnv()

    # Identical to ExecJobRegistry's lambda at ralph/mcp/tools/_exec_jobs.py:290.
    # Keeping a strong reference so we can remove it from the global hook
    # list at teardown (otherwise atexit's _run_shutdown_hooks fan-out fires
    # it on the wrong fake process manager at process exit).
    def session_end_hook() -> None:
        env.registry.shutdown_all("session_end")

    register_shutdown_hook(session_end_hook)

    try:
        job_id = env.registry.start(["long_running"], capability="ProcessExecBounded")
        active_proc = next(iter(env.procs.values()))
        assert active_proc._terminated is False

        # Fire the hook — mirrors what the atexit-callback / SIGTERM signal
        # handler invoke at session / process end.
        session_end_hook()

        # 1. Process tree was terminated (observable via the FakePopen seam).
        assert active_proc._terminated is True
        # 2. The kill_cause recorded on the JobResult is the session-end literal.
        result = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        assert isinstance(result, JobResult)
        assert result.kill_cause == "session_end"
        # 3. No active job remains waitable as "running" in this registry.
        assert job_id not in env.registry._running_jobs
        assert env.registry._running_jobs == {}
    finally:
        # Remove the hook we added so atexit-time _run_shutdown_hooks cannot
        # fire it on the wrong fake process manager at process exit.
        with contextlib.suppress(ValueError):
            _SHUTDOWN_HOOKS.remove(session_end_hook)


def test_restart_lost_fresh_registry_returns_nonretryable_lost_in_server_restart_error() -> None:
    """A fresh post-restart registry treats unknown job_ids as lost in server restart.

    The error is non-retryable (idempotent raise) and never fabricates a
    ``JobSnapshot`` or ``JobResult``. The OS has already reaped the previous
    tree at restart time, so the new ``ProcessManager``'s lack of knowledge
    IS the observable restart signal.

    This proves the B.5 restart contract (WU-4 §5.2): ``exec_wait(old_job_id)``
    on the new registry returns a non-retryable lost-job error and never a
    fabricated result. The previous registry's tree is not observable to the
    new registry — by design — and the new registry cannot poison the
    previous registry's state.
    """
    # "Previous server" — owns a live long-running job; the FakePopen
    # handle still has ``_terminated=False`` until its registry kills it.
    prev = _TestEnv()
    prev_job_id = prev.registry.start(["long_running"], capability="ProcessExecBounded")
    prev_proc = next(iter(prev.procs.values()))

    # A brand-new registry simulating the post-restart server. The fresh
    # ProcessManager has no knowledge of the previous server's PIDs.
    fresh = _TestEnv()
    new_registry = fresh.registry

    # 1. Outside observer waiting on the previous job_id against the NEW
    # registry raises ExecJobError carrying the lost-in-server-restart
    # wording — never returns a fabricated JobSnapshot or JobResult.
    with pytest.raises(ExecJobError) as excinfo:
        new_registry.wait(prev_job_id, wait_ms=0, capability="ProcessExecBounded")
    msg = str(excinfo.value)
    assert "lost in server restart" in msg, msg
    assert prev_job_id in msg

    # 2. Idempotent — re-issuing the same lost-job wait raises the same
    # error. The contract is non-retryable; clients must back off.
    with pytest.raises(ExecJobError) as excinfo_retry:
        new_registry.wait(prev_job_id, wait_ms=0, capability="ProcessExecBounded")
    assert "lost in server restart" in str(excinfo_retry.value)
    assert prev_job_id in str(excinfo_retry.value)

    # 3. A wholly invented job_id (never seen anywhere) yields the same
    # lost-job contract — the new registry cannot distinguish it from
    # ids owned by the prior server, which is the correct behaviour.
    with pytest.raises(ExecJobError) as excinfo_fabricated:
        new_registry.wait("job_never_existed_xyz", wait_ms=0, capability="ProcessExecBounded")
    assert "lost in server restart" in str(excinfo_fabricated.value)

    # 4. The previous registry is untouched: its job is still waitable as a
    # live JobSnapshot — proving the new registry's lost-job decision is
    # independent of (and cannot poison) the previous server's state.
    snapshot = prev.registry.wait(prev_job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(snapshot, JobSnapshot)
    assert snapshot.running is True
    assert prev_proc._terminated is False
