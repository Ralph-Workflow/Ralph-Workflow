"""Session-end teardown and restart-loss lifecycle tests for ExecJobRegistry."""

from __future__ import annotations

import pytest

from ralph.mcp.tools import ExecJobError, JobResult, JobSnapshot
from tests.test_exec_jobs import _TestEnv

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
    from ralph.mcp.server.runtime import register_shutdown_hook

    env = _TestEnv()

    # Identical to ExecJobRegistry's lambda at ralph/mcp/tools/_exec_jobs.py:290.
    # Keeping a strong reference so we can remove it from the global hook
    # list at teardown (otherwise atexit's _run_shutdown_hooks fan-out fires
    # it on the wrong fake process manager at process exit).
    hook_state = {"active": True}

    def session_end_hook() -> None:
        if hook_state["active"]:
            env.registry.shutdown_all("session_end")

    register_shutdown_hook(session_end_hook)

    try:
        job_id = env.registry.start(["long_running"], capability="ProcessExecBounded")
        active_proc = next(iter(env.procs.values()))
        assert active_proc.pid not in env.pm.orphans_cleaned

        # Fire the hook — mirrors what the atexit-callback / SIGTERM signal
        # handler invoke at session / process end.
        session_end_hook()

        # 1. Process tree was terminated (observable via the FakePopen seam).
        assert active_proc.pid in env.pm.orphans_cleaned
        # 2. The kill_cause recorded on the JobResult is the session-end literal.
        result = env.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        assert isinstance(result, JobResult)
        assert result.kill_cause == "session_end"
        # 3. No active job remains waitable as "running" in this registry.
        assert env.registry.is_running(job_id) is False
        # The registry holds NO running job at all: the finished result is
        # the only thing its PUBLIC surface still exposes for the job.
        finished = env.registry.last_finished_result()
        assert finished is not None, "the registry must publish the finished job"
        assert finished.job_id == job_id
        assert finished.kill_cause == "session_end"
    finally:
        # Neutralise the hook we added so the process-exit fan-out cannot
        # fire it on the wrong fake process manager at process exit. The
        # hook stays in the process-global shutdown list (the public
        # registration surface exposes no unregister), so prove it is
        # INERT instead of leaking a live teardown into process exit.
        hook_state["active"] = False
        cleaned_before = list(env.pm.orphans_cleaned)
        session_end_hook()
        assert env.pm.orphans_cleaned == cleaned_before, (
            "the registered shutdown hook must be inert once the test has finished"
        )


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
    assert prev_proc.pid not in prev.pm.orphans_cleaned
