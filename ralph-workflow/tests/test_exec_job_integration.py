"""End-to-end integration tests for exec, exec_wait, and exec_cancel (WU-6).

Drives the real MCP tool registry (ToolBridge) and tool handlers over fake
process and clock seams to verify:
- Soft-budget expiry -> JobSnapshot with liveness verdict and instruction
- Repeated exec_wait polling while still running
- Final success JobResult once the job completes
- Quiet-waiting completion without false stall kill
- Unknown / expired job_id error reporting
- Cancel mid-wait with process tree termination and cancelled result status
- Direct handler parity and parameter validation

Zero real wall-clock sleeps, zero real subprocesses, zero network I/O.
"""

from __future__ import annotations

import io
import itertools
import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.tools._exec_job_tools import (
    EXEC_CANCEL_TOOL,
    EXEC_WAIT_TOOL,
    handle_exec_cancel,
    handle_exec_wait,
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
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    InvalidParamsError,
    ToolContent,
    ToolResult,
)
from ralph.mcp.tools.exec import handle_exec_command
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
from ralph.testing._process_streams import ProcessStreams
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from ralph.process.manager._liveness_sampler import LivenessProc
    from ralph.process.manager._process_manager_types import (
        _SyncProcessFactory,
        _SyncProcessLike,
    )


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


class _TrackingProcessManager(ProcessManager):
    """ProcessManager tracking process handles and cleanups."""

    def __init__(
        self,
        policy: ProcessManagerPolicy | None = None,
        sync_process_factory: _SyncProcessFactory | None = None,
        psutil: FakePsutil | None = None,
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


class _SimulatedExecJobRegistry(ExecJobRegistry):
    """ExecJobRegistry that advances a FakeClock during waits without wall-clock sleep."""

    def __init__(
        self,
        *,
        process_manager: ProcessManager,
        clock: _FakeClock,
        psutil_module: _FakePsutilModule,
        spill_dir: Path | None = None,
    ) -> None:
        self.fake_clock = clock
        super().__init__(
            process_manager=process_manager,
            clock=clock,
            liveness_config=LivenessConfig(
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
            ),
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
        rc = None
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
        return super().wait(job_id, wait_ms=wait_ms, capability=capability, progress=progress)


class _IntegrationTestHarness:
    """Test fixture bundling deterministic clock, fake processes, and tool registry."""

    def __init__(self) -> None:
        self.clock = _FakeClock(1000.0)
        self.pids = itertools.count(100)
        self.procs: dict[int, FakePopen] = {}
        self.sampler_procs: dict[int, _FakeSamplerProc] = {}
        self.psutil_module = _FakePsutilModule(self.sampler_procs)

        def sync_factory(command: Sequence[str], opts: SpawnOptions) -> _SyncProcessLike:
            del command, opts
            pid = next(self.pids)
            p = FakePopen(
                pid=pid,
                state=ProcessState(returncode=None),
                streams=ProcessStreams(stdout=io.BytesIO(b""), stderr=io.BytesIO(b"")),
            )
            self.procs[pid] = p
            self.sampler_procs[pid] = _FakeSamplerProc(pid=pid)
            return p

        self.pm = _TrackingProcessManager(
            policy=ProcessManagerPolicy(default_grace_period_s=0.0),
            sync_process_factory=sync_factory,
            psutil=FakePsutil(),
        )
        self.registry = _SimulatedExecJobRegistry(
            process_manager=self.pm,
            clock=self.clock,
            psutil_module=self.psutil_module,
        )
        set_exec_job_registry(self.registry)

        self.session = AgentSession(
            session_id="integration-session-1",
            run_id="integration-run-1",
            drain="standalone",
            capabilities={"ProcessExecBounded", "ProcessExecUnbounded"},
        )
        self.workspace = MemoryWorkspace()
        self.bridge: ToolBridge = build_ralph_tool_registry(self.session, self.workspace)


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


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


def test_exec_job_flow_soft_budget_expiry_repeated_wait_and_success() -> None:
    """Full lifecycle: soft-budget expiry -> snapshots -> poll -> completed JobResult."""
    harness = _IntegrationTestHarness()

    assert harness.bridge.has_tool("exec")
    assert harness.bridge.has_tool(EXEC_WAIT_TOOL)
    assert harness.bridge.has_tool(EXEC_CANCEL_TOOL)

    # 1. Start long-running job through tool registry dispatch.
    # Soft reply budget expires (45s), returning in-progress JobSnapshot.
    raw_res = harness.bridge.dispatch(
        "exec",
        {"command": "python", "args": ["-c", "import time; time.sleep(100)"]},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    assert isinstance(raw_res, ToolResult)
    assert raw_res.is_error is False

    snap_payload = _parse_payload(raw_res)
    job_id = snap_payload["job_id"]
    assert isinstance(job_id, str)
    assert job_id.startswith("job_")
    assert snap_payload["running"] is True
    assert snap_payload["instruction"] == "call exec_wait; do not re-run"
    assert isinstance(snap_payload["elapsed_s"], (int, float))
    assert snap_payload["elapsed_s"] >= 45.0
    verdict = snap_payload["verdict"]
    assert isinstance(verdict, dict)
    assert "score" in verdict
    assert "stall_window_s" in verdict

    # Process is still running on fake seam
    active_proc = next(iter(harness.procs.values()))
    assert active_proc.poll() is None

    # 2. First exec_wait call: job still in flight, returns next snapshot.
    wait_1 = harness.bridge.dispatch(
        EXEC_WAIT_TOOL,
        {"job_id": job_id, "wait_ms": 10000},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    assert isinstance(wait_1, ToolResult)
    assert wait_1.is_error is False
    payload_1 = _parse_payload(wait_1)
    assert payload_1["job_id"] == job_id
    assert payload_1["running"] is True
    assert payload_1["instruction"] == "call exec_wait; do not re-run"
    assert isinstance(payload_1["elapsed_s"], (int, float))
    assert payload_1["elapsed_s"] >= 55.0

    # 3. Simulate process completing with output
    active_job = harness.registry._running_jobs[job_id]
    with active_job.lock:
        active_job.stdout_head.extend(b"build completed in 55s\n")
        active_job.total_stdout_bytes += len(b"build completed in 55s\n")
        active_job.last_output_at = harness.clock()
    active_proc._returncode = 0

    # 4. Second exec_wait call: detects completion, returns final success result
    wait_2 = harness.bridge.dispatch(
        EXEC_WAIT_TOOL,
        {"job_id": job_id, "wait_ms": 10000},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    assert isinstance(wait_2, ToolResult)
    assert wait_2.is_error is False
    final_payload = _parse_payload(wait_2)
    assert final_payload["job_id"] == job_id
    assert final_payload["running"] is False
    assert final_payload["returncode"] == 0
    assert "build completed in 55s" in str(final_payload["output"])
    assert final_payload["verdict"] is not None


def test_exec_job_quiet_waiting_completion() -> None:
    """A quiet job with zero output completes before stall window without false kill."""
    harness = _IntegrationTestHarness()

    # 1. Start quiet job (no stdout/stderr emitted)
    raw_res = harness.bridge.dispatch(
        "exec",
        {"command": "sleep", "args": ["60"]},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    snap = _parse_payload(raw_res)
    job_id = str(snap["job_id"])
    assert snap["running"] is True
    output_obj = snap.get("output")
    assert isinstance(output_obj, dict)
    output: dict[str, object] = {str(k): v for k, v in output_obj.items()}
    assert output["stdout"] == ""
    assert output["stderr"] == ""

    # 2. Quiet wait while still in progress
    wait_res = harness.bridge.dispatch(
        EXEC_WAIT_TOOL,
        {"job_id": job_id, "wait_ms": 15000},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    wait_payload = _parse_payload(wait_res)
    assert wait_payload["running"] is True
    assert isinstance(wait_payload["elapsed_s"], (int, float))
    assert wait_payload["elapsed_s"] >= 60.0

    # 3. Process finishes quietly with returncode 0 before stall window (90s)
    active_proc = harness.procs[active_proc_pid := next(iter(harness.procs))]
    assert active_proc_pid == 100
    active_proc._returncode = 0

    # 4. Next wait collects the clean quiet exit
    final_res = harness.bridge.dispatch(
        EXEC_WAIT_TOOL,
        {"job_id": job_id},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    final = _parse_payload(final_res)
    assert final["running"] is False
    assert final["returncode"] == 0
    final_output_obj = final.get("output")
    assert isinstance(final_output_obj, dict)
    final_output: dict[str, object] = {str(k): v for k, v in final_output_obj.items()}
    assert final_output["stdout"] == ""
    assert final_output["stderr"] == ""
    assert final_output["truncated"] is False


def test_exec_job_unknown_job_id_error() -> None:
    """Waiting or cancelling an unknown or expired job returns an is_error ToolResult."""
    harness = _IntegrationTestHarness()

    # 1. exec_wait with nonexistent job_id
    wait_err = harness.bridge.dispatch(
        EXEC_WAIT_TOOL,
        {"job_id": "job_nonexistent_xyz"},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    assert isinstance(wait_err, ToolResult)
    assert wait_err.is_error is True
    wait_content = wait_err.content[0]
    assert isinstance(wait_content, ToolContent)
    assert "unknown or expired job" in wait_content.text

    # 2. exec_cancel with nonexistent job_id
    cancel_err = harness.bridge.dispatch(
        EXEC_CANCEL_TOOL,
        {"job_id": "job_nonexistent_xyz"},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    assert isinstance(cancel_err, ToolResult)
    assert cancel_err.is_error is True
    cancel_content = cancel_err.content[0]
    assert isinstance(cancel_content, ToolContent)
    assert "unknown or expired job" in cancel_content.text


def test_exec_job_cancel_mid_wait() -> None:
    """Cancelling a running job terminates the process and marks it cancelled."""
    harness = _IntegrationTestHarness()

    raw_res = harness.bridge.dispatch(
        "exec",
        {"command": "python", "args": ["-c", "long_job"]},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    snap = _parse_payload(raw_res)
    job_id = str(snap["job_id"])
    active_proc = next(iter(harness.procs.values()))
    assert bool(active_proc._terminated) is False

    # Cancel mid-wait
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
    assert bool(active_proc._terminated) is True

    # Subsequent exec_wait reflects cancellation
    wait_after_cancel = harness.bridge.dispatch(
        EXEC_WAIT_TOOL,
        {"job_id": job_id},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    assert isinstance(wait_after_cancel, ToolResult)
    assert wait_after_cancel.is_error is False
    after_payload = _parse_payload(wait_after_cancel)
    assert after_payload["job_id"] == job_id
    assert after_payload["cancelled"] is True


def test_direct_handlers_parity_and_flow() -> None:
    """Direct handler calls (handle_exec_command, handle_exec_wait) match bridge behavior."""
    harness = _IntegrationTestHarness()

    # Call handle_exec_command directly
    exec_res = handle_exec_command(
        harness.session,
        harness.workspace,
        {"command": "python", "args": ["-c", "compute_pi"]},
        registry=harness.registry,
    )
    assert exec_res.is_error is False
    snap = _parse_payload(exec_res)
    job_id = str(snap["job_id"])
    assert snap["running"] is True

    # Direct handle_exec_wait while running
    wait_res = handle_exec_wait(
        harness.session,
        harness.workspace,
        {"job_id": job_id, "wait_ms": 5000},
        registry=harness.registry,
    )
    assert wait_res.is_error is False
    wait_snap = _parse_payload(wait_res)
    assert wait_snap["running"] is True

    # Mark finished
    active_proc = next(iter(harness.procs.values()))
    active_proc._returncode = 0

    # Direct handle_exec_wait on finished job
    final_res = handle_exec_wait(
        harness.session,
        harness.workspace,
        {"job_id": job_id},
        registry=harness.registry,
    )
    assert final_res.is_error is False
    final = _parse_payload(final_res)
    assert final["running"] is False
    assert final["returncode"] == 0


def test_direct_handlers_cancel_mid_wait() -> None:
    """Direct handle_exec_cancel terminates process and updates job status."""
    harness = _IntegrationTestHarness()

    exec_res = handle_exec_command(
        harness.session,
        harness.workspace,
        {"command": "python", "args": ["-c", "train_model"]},
        registry=harness.registry,
    )
    snap = _parse_payload(exec_res)
    job_id = str(snap["job_id"])
    active_proc = next(iter(harness.procs.values()))

    # Direct cancel
    cancel_res = handle_exec_cancel(
        harness.session,
        harness.workspace,
        {"job_id": job_id},
        registry=harness.registry,
    )
    assert cancel_res.is_error is False
    cancel_payload = _parse_payload(cancel_res)
    assert cancel_payload["cancelled"] is True
    assert bool(active_proc._terminated) is True

    # Direct wait on cancelled job
    wait_res = handle_exec_wait(
        harness.session,
        harness.workspace,
        {"job_id": job_id},
        registry=harness.registry,
    )
    assert wait_res.is_error is False
    wait_payload = _parse_payload(wait_res)
    assert wait_payload["cancelled"] is True


def test_exec_job_parameter_validation_and_capability_checks() -> None:
    """Invalid parameters and capability gaps are rejected appropriately."""
    harness = _IntegrationTestHarness()

    # Empty job_id in handle_exec_wait
    with pytest.raises(InvalidParamsError, match="'job_id' must be a non-empty string"):
        handle_exec_wait(harness.session, harness.workspace, {"job_id": ""})

    # Non-string job_id in handle_exec_cancel
    with pytest.raises(InvalidParamsError, match="'job_id' must be a non-empty string"):
        handle_exec_cancel(harness.session, harness.workspace, {"job_id": 12345})

    # Session lacking ProcessExecBounded capability
    unauthorized_session = AgentSession(
        session_id="unauth-1",
        run_id="unauth-run-1",
        drain="standalone",
        capabilities=set(),
    )
    with pytest.raises(CapabilityDeniedError):
        handle_exec_command(
            unauthorized_session,
            harness.workspace,
            {"command": "python", "args": ["-c", "test"]},
            registry=harness.registry,
        )

    with pytest.raises(CapabilityDeniedError):
        handle_exec_wait(
            unauthorized_session,
            harness.workspace,
            {"job_id": "job_123"},
            registry=harness.registry,
        )

    with pytest.raises(CapabilityDeniedError):
        handle_exec_cancel(
            unauthorized_session,
            harness.workspace,
            {"job_id": "job_123"},
            registry=harness.registry,
        )
