from __future__ import annotations

import pytest

from ralph.mcp.tools import ExecJobError, JobResult, JobSnapshot
from tests.test_exec_jobs import _TestEnv


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
