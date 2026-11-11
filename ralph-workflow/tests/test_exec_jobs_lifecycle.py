from __future__ import annotations

import itertools
from collections.abc import Sequence
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools import ExecJobError, ExecJobRegistry, JobResult, JobSnapshot
from ralph.process.manager import ProcessManagerPolicy, SpawnOptions
from ralph.testing.fake_process import FakePsutil, ProcessState
from tests.test_exec_jobs import (
    FakeClock,
    FakePsutilModule,
    FakeSamplerProc,
    TrackingProcessManager,
    _ControllableFakePopen,
    _TestEnv,
)

if TYPE_CHECKING:
    from ralph.process.manager import SyncProcessLike


def test_default_idle_kills_at_90s() -> None:
    from ralph import timeout_defaults as _td
    from ralph.process.manager import default_liveness_config

    cfg = default_liveness_config()
    clock = FakeClock(1000.0)
    pids = itertools.count(100)
    procs: dict[int, _ControllableFakePopen] = {}
    sampler_procs: dict[int, FakeSamplerProc] = {}

    def sync_factory(command: Sequence[str], opts: SpawnOptions) -> SyncProcessLike:
        pid = next(pids)
        p = _ControllableFakePopen(pid=pid, state=ProcessState(returncode=None))
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

    job_id = registry.start(["idle"], timeout_ms=90_000, capability="ProcessExecBounded")

    kill_at: float | None = None
    dt = _td.EXEC_LIVENESS_SAMPLE_INTERVAL_S
    for _ in range(50):
        clock.advance(dt)
        registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        registry.step_job(job_id, dt=dt)
        if not registry.is_running(job_id):
            finished = registry.last_finished_result()
            assert finished is not None
            kill_at = finished.elapsed_s
            break

    assert kill_at is not None, "idle job did not terminate within 100 s"
    assert 88.0 <= kill_at <= 92.0, (
        f"idle kill fired at {kill_at}s, expected 90s ± 1 sample interval "
        f"(88 s..92 s); the EV-002 bug returned 138 s"
    )
    finished = registry.last_finished_result()
    assert finished is not None
    assert finished.kill_cause == "stalled"


def test_session_end_via_registered_shutdown_hook_records_session_end_cause() -> None:
    from ralph.mcp.server.runtime import register_shutdown_hook

    env = _TestEnv()

    hook_state = {"active": True}

    def session_end_hook() -> None:
        if hook_state["active"]:
            env.registry.shutdown_all("session_end")

    register_shutdown_hook(session_end_hook)

    try:
        job_id = env.registry.start(["long_running"], capability="ProcessExecBounded")
        active_proc = next(iter(env.procs.values()))
        assert active_proc.pid not in env.pm.orphans_cleaned

        session_end_hook()

        assert active_proc.pid in env.pm.orphans_cleaned
        result = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        assert isinstance(result, JobResult)
        assert result.kill_cause == "session_end"
        assert env.registry.is_running(job_id) is False
        finished = env.registry.last_finished_result()
        assert finished is not None, "the registry must publish the finished job"
        assert finished.job_id == job_id
        assert finished.kill_cause == "session_end"
    finally:
        hook_state["active"] = False
        cleaned_before = list(env.pm.orphans_cleaned)
        session_end_hook()
        assert env.pm.orphans_cleaned == cleaned_before, (
            "the registered shutdown hook must be inert once the test has finished"
        )


def test_restart_lost_fresh_registry_returns_nonretryable_lost_in_server_restart_error() -> None:
    prev = _TestEnv()
    prev_job_id = prev.registry.start(["long_running"], capability="ProcessExecBounded")
    prev_proc = next(iter(prev.procs.values()))

    fresh = _TestEnv()
    new_registry = fresh.registry

    with pytest.raises(ExecJobError) as excinfo:
        new_registry.wait(prev_job_id, wait_ms=0, capability="ProcessExecBounded")
    msg = str(excinfo.value)
    assert "lost in server restart" in msg, msg
    assert prev_job_id in msg

    with pytest.raises(ExecJobError) as excinfo_retry:
        new_registry.wait(prev_job_id, wait_ms=0, capability="ProcessExecBounded")
    assert "lost in server restart" in str(excinfo_retry.value)
    assert prev_job_id in str(excinfo_retry.value)

    with pytest.raises(ExecJobError) as excinfo_fabricated:
        new_registry.wait("job_never_existed_xyz", wait_ms=0, capability="ProcessExecBounded")
    assert "lost in server restart" in str(excinfo_fabricated.value)

    snapshot = prev.registry.wait(prev_job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(snapshot, JobSnapshot)
    assert snapshot.running is True
    assert prev_proc.pid not in prev.pm.orphans_cleaned
