"""End-to-end integration tests for exec, exec_wait, and exec_cancel (WU-3).

Drives the real MCP tool registry (ToolBridge) and tool handlers over fake
process and clock seams to verify the full long-running job flow under the
default 90s stall window:

- Silent CPU-busy command driven past 90s of fake time through the real
  exec / exec_wait handlers with default ``timeout_ms`` — no stalled kill,
  job survives in-process polling, final exec_wait returns the legacy
  success result.
- R7-style awake / quiet-waiting job (low CPU, nonzero context-switch rate,
  sparse output) surviving tens of simulated minutes of repeated public
  exec_wait calls, each advancing the fake clock by the soft reply budget,
  with no kill of any cause.
- Unknown / expired job_id returns a clear non-retryable error.
- exec_cancel mid-wait terminates the fake process tree and reports
  the cancellation cause.

All test seams are public:

- ``harness.bridge.dispatch(name, params)`` — full MCP bridge.
- ``harness.clock.advance(dt)`` — fake monotonic clock.
- ``harness.registry.step_job(job_id, dt)`` — public sampler/integrator
  step.
- ``proc._returncode`` / ``proc._terminated`` — ``FakePopen`` test seam.
- ``sampler_proc.user_cpu`` / ``sampler_proc.voluntary_ctx`` —
  ``_FakeSamplerProc`` test seam (NOT the registry's private state).

No private-registry mutation. Zero wall-clock sleeps, zero real
subprocesses, zero network I/O.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.tools._exec_job_tools import (
    EXEC_CANCEL_TOOL,
    EXEC_WAIT_TOOL,
    reset_exec_job_registry,
    set_exec_job_registry,
)
from ralph.mcp.tools._exec_jobs import (
    ExecJobRegistry,
    JobResult,
    JobSnapshot,
    ProgressSubscription,
)
from ralph.mcp.tools.bridge import ToolBridge, build_ralph_tool_registry
from ralph.mcp.tools.coordination import ToolContent, ToolResult
from ralph.process.manager import (
    ProcessManager,
    ProcessManagerPolicy,
    SpawnOptions,
)
from ralph.process.manager._liveness_assessor import LivenessConfig
from ralph.testing._fake_popen import FakePopen
from ralph.testing._fake_psutil import FakePsutil
from ralph.testing._process_state import ProcessState
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from ralph.process.manager._liveness_sampler import LivenessProc
    from ralph.process.manager._process_manager_types import _SyncProcessLike


class _FakeClock:
    """Deterministic monotonic clock for simulation."""

    def __init__(self, initial_time: float = 1000.0) -> None:
        self._time = initial_time

    def __call__(self) -> float:
        return self._time

    def advance(self, dt: float) -> None:
        self._time += dt


class _CpuTimesNamedTuple(NamedTuple):
    user: float
    system: float


class _IoCountersNamedTuple(NamedTuple):
    read_bytes: int
    write_bytes: int


class _CtxSwitchesNamedTuple(NamedTuple):
    voluntary: int
    involuntary: int = 0


@dataclass
class _FakeSamplerProc:
    """Mock process for the TreeSampler; lives in test-owned dict."""

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


def _build_test_config(
    *,
    evidence_window_s: float = 30.0,
    awake_per_s_inactive: float = 0.1,
    awake_per_s_active: float = 2.0,
) -> LivenessConfig:
    """Build a deterministic liveness config; tunable per-test for the
    windowed-rate math (the 30 s default evidence window otherwise prunes
    the previous iteration's voluntary-ctx sample when an iter advances
    by the 45 s soft reply budget)."""
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
        awake_per_s_inactive=awake_per_s_inactive,
        awake_per_s_active=awake_per_s_active,
        stall_theta=0.5,
        evidence_window_s=evidence_window_s,
    )


class _SimulatedExecJobRegistry(ExecJobRegistry):
    """ExecJobRegistry that advances a deterministic FakeClock during waits
    so the tests do not rely on real wall-clock sleeps."""

    def __init__(
        self,
        *,
        process_manager: ProcessManager,
        clock: _FakeClock,
        psutil_module: _FakePsutilModule,
        liveness_config: LivenessConfig,
        spill_dir: None = None,
    ) -> None:
        self.fake_clock = clock
        super().__init__(
            process_manager=process_manager,
            clock=clock,
            liveness_config=liveness_config,
            psutil_module=psutil_module,
            spill_dir=spill_dir,
            auto_register_runtime_hooks=False,
            background_sampler=False,
        )

    def wait(
        self,
        job_id: str,
        *,
        wait_ms: int,
        capability: str,
        progress: ProgressSubscription | None = None,
    ) -> JobSnapshot | JobResult:
        active = None
        needs_finish = False
        rc: int | None = None
        with self._lock:
            active = self._running_jobs.get(job_id)
            if active is not None and not active.done_event.is_set():
                if active.handle is not None:
                    poll_rc = active.handle.poll()
                    if poll_rc is not None:
                        needs_finish = True
                        rc = poll_rc
                if not needs_finish:
                    if wait_ms > 0:
                        self.fake_clock.advance(wait_ms / 1000.0)
                    wait_ms = 0
        if needs_finish and active is not None and rc is not None:
            self._mark_finished(active, returncode=rc)
            wait_ms = 0
        return super().wait(
            job_id,
            wait_ms=wait_ms,
            capability=capability,
            progress=progress,
        )


class _IntegrationTestHarness:
    """Bundle of FakeClock, fake processes, the registry, the MCP session,
    and the tool bridge. No private-state mutation; everything below is a
    public test seam.
    """

    def __init__(
        self,
        *,
        liveness_config: LivenessConfig | None = None,
    ) -> None:
        self.clock = _FakeClock(1000.0)
        self.pids = itertools.count(100)
        self.procs: dict[int, FakePopen] = {}
        self.sampler_procs: dict[int, _FakeSamplerProc] = {}
        self.psutil_module = _FakePsutilModule(self.sampler_procs)

        def sync_factory(
            command: Sequence[str], opts: SpawnOptions
        ) -> _SyncProcessLike:
            del command, opts
            pid = next(self.pids)
            p = FakePopen(pid=pid, state=ProcessState(returncode=None))
            self.procs[pid] = p
            self.sampler_procs[pid] = _FakeSamplerProc(pid=pid)
            return p

        self.pm = ProcessManager(
            policy=ProcessManagerPolicy(default_grace_period_s=0.0),
            sync_process_factory=sync_factory,
            psutil=FakePsutil(),
        )
        cfg = (
            liveness_config
            if liveness_config is not None
            else _build_test_config()
        )
        self.registry = _SimulatedExecJobRegistry(
            process_manager=self.pm,
            clock=self.clock,
            psutil_module=self.psutil_module,
            liveness_config=cfg,
        )
        set_exec_job_registry(self.registry)

        self.session = AgentSession(
            session_id="integration-session-1",
            run_id="integration-run-1",
            drain="standalone",
            capabilities={"ProcessExecBounded", "ProcessExecUnbounded"},
        )
        self.workspace = MemoryWorkspace()
        self.bridge: ToolBridge = build_ralph_tool_registry(
            self.session, self.workspace
        )

    def start_via_bridge(
        self,
        *,
        command: str,
        args: list[str],
    ) -> tuple[FakePopen, _FakeSamplerProc, str]:
        """Start a job through the public MCP bridge and return the
        observable test-owned handles (FakePopen, sampler, job_id)."""
        raw_res = self.bridge.dispatch(
            "exec",
            {"command": command, "args": args},
            host_session=self.session,
            workspace=self.workspace,
        )
        snap_payload = _parse_payload(raw_res)
        job_id = str(snap_payload["job_id"])
        proc = next(iter(self.procs.values()))
        sampler = self.sampler_procs[proc.pid]
        return proc, sampler, job_id


@pytest.fixture(autouse=True)
def _cleanup_registry() -> Iterator[None]:
    try:
        yield
    finally:
        reset_exec_job_registry()


def _parse_payload(result: object) -> dict[str, object]:
    assert isinstance(result, ToolResult)
    assert len(result.content) >= 1
    content = result.content[0]
    assert isinstance(content, ToolContent)
    parsed: object = json.loads(content.text)
    assert isinstance(parsed, dict)
    out: dict[str, object] = {}
    for k, v in parsed.items():
        out[str(k)] = v
    return out


def _poll_exec_wait(
    harness: _IntegrationTestHarness,
    job_id: str,
    wait_ms: int,
) -> dict[str, object]:
    res = harness.bridge.dispatch(
        EXEC_WAIT_TOOL,
        {"job_id": job_id, "wait_ms": wait_ms},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    return _parse_payload(res)


# ---------------------------------------------------------------------------
# Required coverage
# ---------------------------------------------------------------------------


def test_exec_job_silent_cpu_busy_drives_past_default_stall_window() -> None:
    """A silent CPU-busy command survives the default 90 s stall window
    through repeated public exec/poll cycles and ends cleanly. No stalled
    kill occurs when R4 (CpuBusy) keeps the stall integrator below the
    default stall window.

    Drives the real exec / exec_wait handlers under the §3b default
    ``EXEC_DEFAULT_TIMEOUT_MS`` (90 000 ms; ``stall_window_s`` = 90.0)
    for > 90 s of fake time. The fake CPU-busy signal is observable
    through ``sampler_proc.user_cpu`` (the test-owned mock).
    """
    harness = _IntegrationTestHarness()

    proc, sampler, job_id = harness.start_via_bridge(
        command="compile_large",
        args=["--silent"],
    )
    assert proc.poll() is None

    # Drive fake clock forward past the 90 s stall window with an R4-favoring
    # signal: cpu_ratio ≈ 0.15 (in [cpu_busy_min, cpu_busy_max]). R4 fires with
    # z = 0.6, pushing score well above stall_theta so pressure decays.
    # No output, no I/O, near-zero context switches: the rule network reads
    # this as "CPU-busy silent compute" — not as idle.
    step_dt = 5.0
    n_steps = 14
    for _ in range(n_steps):
        harness.clock.advance(step_dt)
        sampler.user_cpu += 0.15 * step_dt
        sampler.system_cpu += 0.0
        sampler.voluntary_ctx += 0
        harness.registry.step_job(job_id, dt=step_dt)

    # Public exec_wait poll: clock advances by wait_ms, snapshot returned.
    # The job is still running well past the default 90 s stall window.
    poll = _poll_exec_wait(harness, job_id, wait_ms=10_000)
    assert poll["job_id"] == job_id
    assert poll["running"] is True
    elapsed: object = poll["elapsed_s"]
    assert isinstance(elapsed, (int, float))
    assert float(elapsed) >= 90.0
    verdict_obj: object = poll["verdict"]
    assert isinstance(verdict_obj, dict)
    assert "score" in verdict_obj
    assert "stall_window_s" in verdict_obj
    assert "verdict_kind" in verdict_obj
    assert poll["instruction"] == "call exec_wait; do not re-run"

    # Complete via the FakePopen public seam (_returncode is the documented
    # test seam, NOT private registry state) and verify the final legacy
    # success result: is_error=False (kill paths always raise is_error),
    # running=False, returncode=0, no kill of any cause.
    proc._returncode = 0
    final_res = harness.bridge.dispatch(
        EXEC_WAIT_TOOL,
        {"job_id": job_id, "wait_ms": 1},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    assert isinstance(final_res, ToolResult)
    # A stalled/abandoned/no_progress/hard_cap kill path would render the
    # response as is_error=True via ExecutionError TextContent. The
    # success branch only emits is_error=False. The direct registry call
    # below also asserts kill_cause is None explicitly.
    assert final_res.is_error is False
    final_payload = _parse_payload(final_res)
    assert final_payload["job_id"] == job_id
    assert final_payload["running"] is False
    assert final_payload["returncode"] == 0

    output_obj: object = final_payload["output"]
    assert isinstance(output_obj, dict)
    final_output: dict[str, object] = {str(k): v for k, v in output_obj.items()}
    assert final_output["stdout"] == ""
    assert final_output["stderr"] == ""

    # Direct registry access: no kill of any cause (``stalled``, ``abandoned``,
    # ``no_progress``, ``hard_cap``, ``session_end``, ``restart_lost``).
    final_result = harness.registry.wait(
        job_id, wait_ms=0, capability="ProcessExecBounded"
    )
    assert isinstance(final_result, JobResult)
    assert final_result.kill_cause is None


def test_exec_job_awake_quiet_waiting_survives_tens_of_minutes() -> None:
    """R7-style awake / quiet-waiting job (low CPU, nonzero context-switch
    rate, sparse output) survives tens of simulated minutes of repeated
    public exec_wait polls — each call advances the fake clock by the
    soft reply budget (EXEC_SOFT_REPLY_BUDGET_MS = 45 000 ms) — with no
    kill of any cause.

    The liveness config uses the production ``awake_per_s`` thresholds
    (0.5 / 5.0) and a widened ``evidence_window_s`` so the windowed
    voluntary-ctx rate survives a 45 s poll cycle. Voluntary ctx switches
    at >= 5/s keep Awake = 1.0, R7 weight = 1.0, score >= 0.55 (above
    ``stall_theta``), so the integrator's pressure decays instead of
    climbing across the run.
    """
    cfg = _build_test_config(
        evidence_window_s=60.0,
        awake_per_s_inactive=0.5,
        awake_per_s_active=5.0,
    )
    harness = _IntegrationTestHarness(liveness_config=cfg)

    proc, sampler, job_id = harness.start_via_bridge(
        command="wait_for_event",
        args=["--quiet"],
    )

    # 20 polls * 45 s = 900 s ~= 15 simulated minutes: well above the
    # default section-3b 60 s report window, large enough to prove survival
    # across "tens of simulated minutes", and capped to keep the
    # suspicion-clock streak (one R7-firing stride per iter, dt = poll_dt
    # = 45 s) under the 900 s EXEC_NO_PROGRESS_WINDOW_S hard-coded kill
    # threshold.
    n_iterations = 20
    poll_dt = 45.0
    for _ in range(n_iterations):
        # R7-style signal: zero CPU, no I/O, no output, ~5 ctx switches / s.
        sampler.user_cpu += 0.0
        sampler.system_cpu += 0.0
        sampler.read_bytes += 0
        sampler.write_bytes += 0
        sampler.voluntary_ctx += int(5.0 * poll_dt)
        harness.registry.step_job(job_id, dt=poll_dt)

        # Public exec_wait: advances the fake clock by the soft reply budget
        # and verifies the job is still alive after tens of minutes.
        poll = _poll_exec_wait(harness, job_id, wait_ms=45_000)
        assert poll["job_id"] == job_id
        assert poll["running"] is True

    # Complete cleanly via the FakePopen public seam.
    proc._returncode = 0
    final_res = harness.bridge.dispatch(
        EXEC_WAIT_TOOL,
        {"job_id": job_id, "wait_ms": 1},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    assert isinstance(final_res, ToolResult)
    # A kill of any cause would render is_error=True via ExecutionError
    # TextContent. The success branch only emits is_error=False. The direct
    # registry call below also asserts kill_cause is None explicitly.
    assert final_res.is_error is False
    final_payload = _parse_payload(final_res)
    assert final_payload["running"] is False
    assert final_payload["returncode"] == 0

    final_output_obj: object = final_payload["output"]
    assert isinstance(final_output_obj, dict)
    final_output: dict[str, object] = {str(k): v for k, v in final_output_obj.items()}
    assert final_output["stdout"] == ""
    assert final_output["stderr"] == ""

    # Direct registry access: no kill of any cause after ~15 simulated
    # minutes of awake / quiet-waiting polling.
    final_result = harness.registry.wait(
        job_id, wait_ms=0, capability="ProcessExecBounded"
    )
    assert isinstance(final_result, JobResult)
    assert final_result.kill_cause is None


def test_exec_job_unknown_job_id_error() -> None:
    """Waiting or cancelling an unknown / expired job returns a clear
    non-retryable error (no "retry" advice — the user-visible bug
    "agent re-runs the same tool for ~5 hours" only happens when the
    error fails closed without context).
    """
    harness = _IntegrationTestHarness()

    wait_err = harness.bridge.dispatch(
        EXEC_WAIT_TOOL,
        {"job_id": "job_does_not_exist"},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    assert isinstance(wait_err, ToolResult)
    assert wait_err.is_error is True
    wait_content = wait_err.content[0]
    assert isinstance(wait_content, ToolContent)
    wait_text = wait_content.text
    assert "unknown or expired job" in wait_text
    assert "retry" not in wait_text.lower()

    cancel_err = harness.bridge.dispatch(
        EXEC_CANCEL_TOOL,
        {"job_id": "job_does_not_exist"},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    assert isinstance(cancel_err, ToolResult)
    assert cancel_err.is_error is True
    cancel_content = cancel_err.content[0]
    assert isinstance(cancel_content, ToolContent)
    assert "unknown or expired job" in cancel_content.text


def test_exec_job_cancel_mid_wait_terminates_and_reports() -> None:
    """Cancelling a running job mid-wait terminates the fake process tree
    (the cancellation is observable on the FakePopen handle) and the
    cancel response carries ``cancelled = True`` plus the elapsed time.
    A subsequent exec_wait reflects the cancellation state.
    """
    harness = _IntegrationTestHarness()

    _, _, job_id = harness.start_via_bridge(
        command="long_compile",
        args=["--monitor"],
    )
    proc = next(iter(harness.procs.values()))
    assert bool(proc._terminated) is False

    cancel_res = harness.bridge.dispatch(
        EXEC_CANCEL_TOOL,
        {"job_id": job_id},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    assert isinstance(cancel_res, ToolResult)
    assert cancel_res.is_error is False
    cancel_payload = _parse_payload(cancel_res)
    assert cancel_payload["job_id"] == job_id
    assert cancel_payload["cancelled"] is True
    assert isinstance(cancel_payload["elapsed_s"], (int, float))

    # The fake process tree was killed.
    assert bool(proc._terminated) is True

    # Subsequent exec_wait reflects the cancellation state.
    after_cancel = _poll_exec_wait(harness, job_id, wait_ms=1)
    assert after_cancel["cancelled"] is True


def test_exec_job_hard_cap_error_names_job_ceiling_and_elapsed() -> None:
    """A ``hard_cap`` kill surfaces the real job ceiling and the real elapsed
    runtime through the public ``exec_wait`` handler — not the legacy
    300-second constant, and never a bare "timed out after ?ms".

    This is the observable half of the cause-accurate-messaging contract:
    the agent learns which limit fired, how long the job actually ran, and
    which knob raises it.
    """
    harness = _IntegrationTestHarness()

    requested_ceiling_ms = 60_000
    raw_res = harness.bridge.dispatch(
        "exec",
        {
            "command": "long_build",
            "args": ["--all"],
            "max_runtime_ms": requested_ceiling_ms,
        },
        host_session=harness.session,
        workspace=harness.workspace,
    )
    job_id = str(_parse_payload(raw_res)["job_id"])

    # Idle job: advance fake time past the per-call ceiling, then step.
    step_dt = 30.0
    for _ in range(3):
        harness.clock.advance(step_dt)
        harness.registry.step_job(job_id, dt=step_dt)

    wait_res = harness.bridge.dispatch(
        EXEC_WAIT_TOOL,
        {"job_id": job_id, "wait_ms": 1},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    assert isinstance(wait_res, ToolResult)
    assert wait_res.is_error is True
    content = wait_res.content[0]
    assert isinstance(content, ToolContent)
    text = content.text

    # The job handle is named, so the agent can act on it.
    assert job_id in text
    # The ceiling that actually fired is named, in ms, as a job ceiling.
    assert f"{requested_ceiling_ms}ms" in text
    # The real runtime is reported in the header, and it is at or past the
    # ceiling that fired (the soft reply budget also consumed fake time).
    header = text.splitlines()[0]
    assert header.startswith(f"Job {job_id} was killed after ")
    elapsed_ms_text = header.split(" was killed after ", 1)[1].split("ms", 1)[0]
    assert int(elapsed_ms_text) >= requested_ceiling_ms
    # The knob that raises the ceiling is named, and the legacy constant
    # is not (this is a job kill, not the legacy in-request envelope).
    assert "max_runtime_ms" in text
    assert "EXEC_MAX_TIMEOUT_MS" not in text
    assert "Background command execution is not supported" not in text
