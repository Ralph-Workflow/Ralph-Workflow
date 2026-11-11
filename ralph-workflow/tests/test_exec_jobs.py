from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

import pytest

from ralph.mcp.tools import (
    ExecJobError,
    ExecJobRegistry,
    JobResult,
    JobSnapshot,
    ProgressSubscription,
)
from ralph.process.manager import (
    LivenessConfig,
    ManagedAsyncProcess,
    ManagedProcess,
    ManagedPtyProcess,
    ProcessManager,
    ProcessManagerPolicy,
    SpawnOptions,
)
from ralph.testing.fake_process import (
    FakePopen,
    FakePsutil,
    ProcessState,
)
from ralph.timeout_defaults import (
    EXEC_JOB_ABANDON_S,
    EXEC_JOB_RESULT_FIFO,
    EXEC_MAX_RUNNING_JOBS,
)

if TYPE_CHECKING:
    from ralph.process.manager import (
        LivenessProc,
        PsutilModuleLike,
        SyncProcessFactory,
        SyncProcessLike,
    )


class _ControllableFakePopen(FakePopen):
    def finish(self, returncode: int) -> None:
        self._returncode = returncode


class _FakeClock:
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
    def __init__(
        self,
        policy: ProcessManagerPolicy | None = None,
        sync_process_factory: SyncProcessFactory | None = None,
        psutil: PsutilModuleLike | None = None,
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
    __test__ = False

    def __init__(self, liveness_config: LivenessConfig | None = None) -> None:
        self.clock = FakeClock(1000.0)
        self.pids = itertools.count(100)
        self.procs: dict[int, _ControllableFakePopen] = {}
        self.sampler_procs: dict[int, FakeSamplerProc] = {}
        self.psutil_module = FakePsutilModule(self.sampler_procs)

        def sync_factory(command: Sequence[str], opts: SpawnOptions) -> SyncProcessLike:
            pid = next(self.pids)
            p = _ControllableFakePopen(pid=pid, state=ProcessState(returncode=None))
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
            liveness_config=liveness_config if liveness_config is not None else _make_test_config(),
            psutil_module=self.psutil_module,
            auto_register_runtime_hooks=False,
            background_sampler=False,
        )


def test_job_finish_in_budget() -> None:
    env = _TestEnv()
    job_id = env.registry.start(["echo", "hello"], capability="ProcessExecBounded")

    active_proc = next(iter(env.procs.values()))
    active_proc.finish(0)

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

    snapshot = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(snapshot, JobSnapshot)
    assert snapshot.job_id == job_id
    assert snapshot.running
    assert snapshot.instruction == "call exec_wait; do not re-run"

    active_proc = next(iter(env.procs.values()))
    active_proc.finish(0)
    env.registry.step_job(job_id)

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
    assert active_proc.pid in env.pm.orphans_cleaned


def test_job_abandonment() -> None:
    env = _TestEnv()
    job_id = env.registry.start(["long_running"], capability="ProcessExecBounded")
    active_proc = next(iter(env.procs.values()))

    env.clock.advance(EXEC_JOB_ABANDON_S + 1.0)
    env.registry.step_job(job_id)

    result = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(result, JobResult)
    assert result.kill_cause == "abandoned"
    assert active_proc.pid in env.pm.orphans_cleaned


def test_job_stalled() -> None:
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
    env = _TestEnv(liveness_config=fast_config)

    job_id = env.registry.start(["idle_command"], timeout_ms=5_000, capability="ProcessExecBounded")
    active_proc = next(iter(env.procs.values()))

    for _ in range(10):
        env.clock.advance(1.0)
        env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        env.registry.step_job(job_id, dt=1.0)

    result = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(result, JobResult)
    assert result.kill_cause == "stalled"
    assert active_proc.pid in env.pm.orphans_cleaned


def test_job_no_progress() -> None:
    env = _TestEnv()
    job_id = env.registry.start(["spin"], capability="ProcessExecBounded")
    active_proc = next(iter(env.procs.values()))
    sampler_proc = env.sampler_procs[active_proc.pid]

    for _ in range(105):
        env.clock.advance(10.0)
        sampler_proc.user_cpu += 8.0
        env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        env.registry.step_job(job_id, dt=10.0)

    result = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(result, JobResult)
    assert result.kill_cause == "no_progress"
    assert active_proc.pid in env.pm.orphans_cleaned


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

    env.registry.cancel(jobs[0], capability="ProcessExecBounded")
    j5 = env.registry.start(["sleep_5th"], capability="ProcessExecBounded")
    assert j5 is not None


@pytest.mark.timeout_seconds(5.0)
def test_finished_results_fifo_cap() -> None:
    env = _TestEnv()
    jobs: list[str] = []

    for i in range(EXEC_JOB_RESULT_FIFO + 1):
        jid = env.registry.start([f"cmd_{i}"], capability="ProcessExecBounded")
        proc = list(env.procs.values())[-1]
        proc.finish(0)
        env.registry.step_job(jid)
        jobs.append(jid)

    try:
        env.registry.wait(jobs[0], wait_ms=0, capability="ProcessExecBounded")
        raise AssertionError("Expected ExecJobError for evicted job")
    except ExecJobError as exc:
        assert "unknown or expired job" in str(exc)

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

    env.clock.advance(2.0)
    env.registry.step_job(job_id, dt=2.0)

    assert len(frames) == 1
    assert frames[0]["progressToken"] == "tok-abc"
    assert frames[0]["job_id"] == job_id

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

    active_proc = next(iter(env.procs.values()))
    active_proc.finish(0)
    env.registry.step_job(job_id)

    env.clock.advance(2.0)
    env.registry.step_job(job_id, dt=2.0)
    assert len(frames) == 2
    assert len(wait_frames) == 1


def test_trace_1_root_exit_with_orphaned_child() -> None:
    env = _TestEnv()
    job_id = env.registry.start(["sh", "-c", "sleep 100 &"], capability="ProcessExecBounded")
    root_proc = next(iter(env.procs.values()))

    root_proc.finish(0)

    env.registry.step_job(job_id)

    result = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(result, JobResult)
    assert result.returncode == 0
    assert result.kill_cause is None
    assert root_proc.pid in env.pm.orphans_cleaned


def test_trace_2_hard_cap_at_ceiling() -> None:
    env = _TestEnv()
    job_id = env.registry.start(["long_build"], capability="ProcessExecBounded")
    root_proc = next(iter(env.procs.values()))
    for _ in range(121):
        env.clock.advance(30.0)
        env.registry.note_output(job_id, capability="ProcessExecBounded")
        env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        env.registry.step_job(job_id, dt=30.0)

    result = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(result, JobResult)
    assert result.kill_cause == "hard_cap"
    assert root_proc.pid in env.pm.orphans_cleaned


@pytest.mark.timeout_seconds(5)
def test_randomized_hard_cap_terminates_healthily_within_budget_at_real_registry_branch() -> None:
    import math
    import random

    from ralph import timeout_defaults as _td

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
        mid_snapshot: JobSnapshot | None = None
        killed_at_step = 0

        for step_idx in range(max_steps):
            env.clock.advance(sample_interval_s)
            sampler_proc.user_cpu += 0.5 * sample_interval_s
            sampler_proc.read_bytes += int(128 * 1024 * sample_interval_s)
            sampler_proc.voluntary_ctx += int(20 * sample_interval_s)
            env.registry.note_output(job_id, capability="ProcessExecBounded")
            pending = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
            if step_idx == precondition_step and isinstance(pending, JobSnapshot):
                mid_snapshot = pending
            env.registry.step_job(job_id, dt=sample_interval_s)
            if not env.registry.is_running(job_id):
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
        assert result.ceiling_ms == ceiling_ms, "; ".join(case_log)
        assert result.elapsed_s >= effective_ceiling_s - timing_eps, "; ".join(case_log)
        assert result.elapsed_s <= effective_ceiling_s + sample_interval_s + timing_eps, "; ".join(
            case_log
        )

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

        assert root_proc.pid in env.pm.orphans_cleaned, "; ".join(case_log)
        assert env.registry.is_running(job_id) is False, "; ".join(case_log)
        finished = env.registry.last_finished_result()
        assert finished is not None, "; ".join(case_log)
        assert finished.job_id == job_id, "; ".join(case_log)


def test_trace_3_three_hour_e2e_completing_via_max_runtime() -> None:
    env = _TestEnv()
    job_id = env.registry.start(
        ["e2e_test"],
        max_runtime_ms=4 * 3600 * 1000,
        capability="ProcessExecBounded",
    )
    root_proc = next(iter(env.procs.values()))
    for _ in range(240):
        env.clock.advance(30.0)
        env.registry.note_output(job_id, capability="ProcessExecBounded")
        env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        env.registry.step_job(job_id, dt=30.0)

    snap = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(snap, JobSnapshot)
    assert snap.running

    for _ in range(120):
        env.clock.advance(30.0)
        env.registry.note_output(job_id, capability="ProcessExecBounded")
        env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        env.registry.step_job(job_id, dt=30.0)

    root_proc.finish(0)
    env.registry.step_job(job_id, dt=1.0)

    final = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(final, JobResult)
    assert final.returncode == 0
    assert final.kill_cause is None


def test_trace_4_disconnected_request_scenario() -> None:
    env = _TestEnv()
    job_id = env.registry.start(["render_video"], capability="ProcessExecBounded")
    root_proc = next(iter(env.procs.values()))

    snap = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(snap, JobSnapshot)

    env.clock.advance(30.0)
    root_proc.finish(0)
    env.registry.step_job(job_id, dt=30.0)

    reconnect_result = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(reconnect_result, JobResult)
    assert reconnect_result.returncode == 0
    assert reconnect_result.kill_cause is None
