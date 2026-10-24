"""Lifecycle regressions for ExecJobRegistry.

These cases exercise the bounded liveness and session-teardown contracts that are
kept separate from the basic registry and gating tests in ``test_exec_jobs``.
The shared deterministic fakes remain in that module so both suites exercise
one implementation of the test environment.
"""

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

    # Start an idle job with the production 90 s default stall window.
    # ``timeout_ms`` is the §3b ``stall_window``; 90 s is the production
    # ``EXEC_DEFAULT_TIMEOUT_MS`` and the default registered by
    # ``ralph.timeout_defaults``.
    job_id = registry.start(["idle"], timeout_ms=90_000, capability="ProcessExecBounded")
    # The registry is created fresh in this test, so the job owns a fresh
    # stall integrator and no prior history can bleed into the regression.

    kill_at: float | None = None
    dt = _td.EXEC_LIVENESS_SAMPLE_INTERVAL_S  # 2 s
    # 50 ticks * 2 s = 100 s ceiling; the kill must fire well before.
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
    # 90 s ± one sample interval (2 s). Strict bounds keep the
    # regression loud if either the integrator re-opens the
    # ``start_time=last_output_at`` credit or drifts further.
    assert 88.0 <= kill_at <= 92.0, (
        f"idle kill fired at {kill_at}s, expected 90s ± 1 sample interval "
        f"(88 s..92 s); the EV-002 bug returned 138 s"
    )
    finished = registry.last_finished_result()
    assert finished is not None
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
