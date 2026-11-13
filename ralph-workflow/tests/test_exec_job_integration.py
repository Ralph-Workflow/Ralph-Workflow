from __future__ import annotations

import io
import itertools
import json
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

import pytest

from ralph import timeout_defaults as td
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
from ralph.process.manager._liveness_assessor import LivenessConfig, default_liveness_config
from ralph.testing._fake_popen import FakePopen
from ralph.testing._fake_psutil import FakePsutil
from ralph.testing._process_state import ProcessState
from ralph.testing._process_streams import ProcessStreams
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer

    from ralph.process.manager._liveness_sampler import LivenessProc

_PIPE_POLL_S: float = 0.05
_PIPE_ACK_TIMEOUT_S: float = 0.5
_PIPE_EOF_TIMEOUT_S: float = 0.5

_E2E_TOTAL_S: float = 2700.0
_E2E_OUTPUT_PULSE_EVERY_S: float = 180.0

_E2E_TREE_RATES: tuple[tuple[float, float], ...] = (
    (0.004, 8.0),
    (0.004, 8.0),
    (0.002, 4.0),
)


class _FakeClock:
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
    Process: Callable[[int], LivenessProc]

    def __init__(self, processes: dict[int, _FakeSamplerProc] | None = None) -> None:
        self.procs: dict[int, _FakeSamplerProc] = processes if processes is not None else {}
        self.Process = self._get_process

    def _get_process(self, pid: int) -> LivenessProc:
        if pid not in self.procs:
            return _FakeSamplerProc(pid=pid)
        return self.procs[pid]


class _QuietOutputPipe(io.RawIOBase):
    def __init__(self) -> None:
        super().__init__()
        self._cond = threading.Condition()
        self._pending = bytearray()
        self._delivered = 0
        self._acked = 0
        self._finished = False
        self._eof_seen = False

    def emit(self, line: str) -> None:
        with self._cond:
            self._pending.extend(f"{line}\n".encode())
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._finished = True
            self._cond.notify_all()

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: WriteableBuffer, /) -> int:
        with self._cond:
            if self._delivered:
                self._acked += 1
                self._cond.notify_all()
            while not self._pending and not self._finished:
                self._cond.wait(timeout=_PIPE_POLL_S)
            if not self._pending:
                self._eof_seen = True
                self._cond.notify_all()
                return 0
            view = memoryview(buffer)
            chunk = bytes(self._pending[: view.nbytes])
            del self._pending[: len(chunk)]
            self._delivered += len(chunk)
            view[: len(chunk)] = chunk
            return len(chunk)

    def wait_acked(self, min_acks: int, timeout_s: float) -> bool:
        return self._await(lambda: self._acked >= min_acks, timeout_s)

    def wait_closed(self, timeout_s: float) -> bool:
        return self._await(lambda: self._eof_seen, timeout_s)

    def _await(self, ready: Callable[[], bool], timeout_s: float) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: ready() or self._finished, timeout=timeout_s)


class _E2EFakeProcess(FakePopen):
    def __init__(self, *, pid: int, stdout_pipe: _QuietOutputPipe) -> None:
        super().__init__(
            pid=pid,
            state=ProcessState(returncode=None),
            streams=ProcessStreams(stdout=io.BufferedReader(stdout_pipe)),
        )
        self._stdout_pipe = stdout_pipe

    def terminate(self, **_kwargs: object) -> None:
        super().terminate()
        self._stdout_pipe.close()

    def kill(self, **_kwargs: object) -> None:
        super().kill()
        self._stdout_pipe.close()


def _default_fake_process(pid: int, root: _FakeSamplerProc) -> FakePopen:
    del root
    return FakePopen(pid=pid, state=ProcessState(returncode=None))


def _build_test_config(
    *,
    evidence_window_s: float = 30.0,
    awake_per_s_inactive: float = 0.1,
    awake_per_s_active: float = 2.0,
) -> LivenessConfig:
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


class _SampledWaitExecJobRegistry(_SimulatedExecJobRegistry):
    def __init__(
        self,
        *,
        on_sample: Callable[[float], None],
        process_manager: ProcessManager,
        clock: _FakeClock,
        psutil_module: _FakePsutilModule,
        liveness_config: LivenessConfig,
    ) -> None:
        self.on_sample = on_sample
        super().__init__(
            process_manager=process_manager,
            clock=clock,
            psutil_module=psutil_module,
            liveness_config=liveness_config,
        )

    def wait(
        self,
        job_id: str,
        *,
        wait_ms: int,
        capability: str,
        progress: ProgressSubscription | None = None,
    ) -> JobSnapshot | JobResult:
        remaining_s = wait_ms / 1000.0
        while remaining_s > 0.0:
            slice_s = min(td.EXEC_LIVENESS_SAMPLE_INTERVAL_S, remaining_s)
            self.fake_clock.advance(slice_s)
            self.on_sample(slice_s)
            self.step_job(job_id)
            remaining_s -= slice_s
        return super().wait(job_id, wait_ms=0, capability=capability, progress=progress)


class _IntegrationTestHarness:
    def __init__(
        self,
        *,
        liveness_config: LivenessConfig | None = None,
        make_process: Callable[[int, _FakeSamplerProc], FakePopen] | None = None,
    ) -> None:
        self.clock = _FakeClock(1000.0)
        self.pids = itertools.count(100)
        self.procs: dict[int, FakePopen] = {}
        self.sampler_procs: dict[int, _FakeSamplerProc] = {}
        self.psutil_module = _FakePsutilModule(self.sampler_procs)
        build = make_process if make_process is not None else _default_fake_process

        def sync_factory(command: Sequence[str], opts: SpawnOptions) -> FakePopen:
            del command, opts
            pid = next(self.pids)
            root = _FakeSamplerProc(pid=pid)
            self.sampler_procs[pid] = root
            p = build(pid, root)
            self.procs[pid] = p
            return p

        self.pm = ProcessManager(
            policy=ProcessManagerPolicy(default_grace_period_s=0.0),
            sync_process_factory=sync_factory,
            psutil=FakePsutil(),
        )
        cfg = liveness_config if liveness_config is not None else _build_test_config()
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
        self.bridge: ToolBridge = build_ralph_tool_registry(self.session, self.workspace)

    def start_via_bridge(
        self,
        *,
        command: str,
        args: list[str],
    ) -> tuple[FakePopen, _FakeSamplerProc, str]:
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


class _E2EQuietWaitHarness(_IntegrationTestHarness):
    def __init__(self, *, liveness_config: LivenessConfig) -> None:
        self.stdout_pipe = _QuietOutputPipe()
        self.trace_rates: dict[int, tuple[float, float]] = {}
        self.elapsed_s = 0.0
        super().__init__(
            liveness_config=liveness_config,
            make_process=self._make_process,
        )
        self.registry = _SampledWaitExecJobRegistry(
            on_sample=self.advance_trace,
            process_manager=self.pm,
            clock=self.clock,
            psutil_module=self.psutil_module,
            liveness_config=liveness_config,
        )
        set_exec_job_registry(self.registry)

    def _make_process(self, pid: int, root: _FakeSamplerProc) -> FakePopen:
        for cpu_per_s, ctx_per_s in _E2E_TREE_RATES[1:]:
            child = _FakeSamplerProc(pid=next(self.pids))
            root.child_procs.append(child)
            self.sampler_procs[child.pid] = child
            self.trace_rates[child.pid] = (cpu_per_s, ctx_per_s)
        self.trace_rates[pid] = _E2E_TREE_RATES[0]
        return _E2EFakeProcess(pid=pid, stdout_pipe=self.stdout_pipe)

    def advance_trace(self, dt: float) -> None:
        self.elapsed_s += dt
        for pid, (cpu_per_s, ctx_per_s) in self.trace_rates.items():
            node = self.sampler_procs[pid]
            node.user_cpu = cpu_per_s * self.elapsed_s
            node.system_cpu = 0
            node.read_bytes = 0
            node.write_bytes = 0
            node.voluntary_ctx = int(ctx_per_s * self.elapsed_s)


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


def test_exec_job_silent_cpu_busy_drives_past_default_stall_window() -> None:
    harness = _IntegrationTestHarness()

    proc, sampler, job_id = harness.start_via_bridge(
        command="compile_large",
        args=["--silent"],
    )
    assert proc.poll() is None

    step_dt = 5.0
    n_steps = 14
    for _ in range(n_steps):
        harness.clock.advance(step_dt)
        sampler.user_cpu += 0.15 * step_dt
        sampler.system_cpu += 0.0
        sampler.voluntary_ctx += 0
        harness.registry.step_job(job_id, dt=step_dt)

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

    proc._returncode = 0
    final_res = harness.bridge.dispatch(
        EXEC_WAIT_TOOL,
        {"job_id": job_id, "wait_ms": 1},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    assert isinstance(final_res, ToolResult)
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

    final_result = harness.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(final_result, JobResult)
    assert final_result.kill_cause is None


def test_exec_job_awake_quiet_waiting_survives_tens_of_minutes() -> None:
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

    n_iterations = 20
    poll_dt = 45.0
    for _ in range(n_iterations):
        sampler.user_cpu += 0.0
        sampler.system_cpu += 0.0
        sampler.read_bytes += 0
        sampler.write_bytes += 0
        sampler.voluntary_ctx += int(5.0 * poll_dt)
        harness.registry.step_job(job_id, dt=poll_dt)

        poll = _poll_exec_wait(harness, job_id, wait_ms=45_000)
        assert poll["job_id"] == job_id
        assert poll["running"] is True

    proc._returncode = 0
    final_res = harness.bridge.dispatch(
        EXEC_WAIT_TOOL,
        {"job_id": job_id, "wait_ms": 1},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    assert isinstance(final_res, ToolResult)
    assert final_res.is_error is False
    final_payload = _parse_payload(final_res)
    assert final_payload["running"] is False
    assert final_payload["returncode"] == 0

    final_output_obj: object = final_payload["output"]
    assert isinstance(final_output_obj, dict)
    final_output: dict[str, object] = {str(k): v for k, v in final_output_obj.items()}
    assert final_output["stdout"] == ""
    assert final_output["stderr"] == ""

    final_result = harness.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(final_result, JobResult)
    assert final_result.kill_cause is None


def test_exec_job_unknown_job_id_error() -> None:
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

    assert bool(proc._terminated) is True

    after_cancel = _poll_exec_wait(harness, job_id, wait_ms=1)
    assert after_cancel["cancelled"] is True


def test_exec_job_hard_cap_error_names_job_ceiling_and_elapsed() -> None:
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

    assert job_id in text
    assert f"{requested_ceiling_ms}ms" in text
    header = text.splitlines()[0]
    assert header.startswith(f"Job {job_id} was killed after ")
    elapsed_ms_text = header.split(" was killed after ", 1)[1].split("ms", 1)[0]
    assert int(elapsed_ms_text) >= requested_ceiling_ms
    assert "max_runtime_ms" in text
    assert "EXEC_MAX_TIMEOUT_MS" not in text
    assert "Background command execution is not supported" not in text


def _poll_quiet_waiting_to_end(harness: _E2EQuietWaitHarness, job_id: str) -> tuple[int, str, str]:
    poll_s = td.EXEC_SOFT_REPLY_BUDGET_MS / 1000.0
    n_polls = int((_E2E_TOTAL_S - harness.elapsed_s) // poll_s)
    pulses = 0
    first_line = ""
    last_line = ""
    for poll_index in range(n_polls):
        snapshot = _poll_exec_wait(harness, job_id, wait_ms=td.EXEC_SOFT_REPLY_BUDGET_MS)
        assert snapshot["job_id"] == job_id
        assert snapshot["running"] is True
        assert snapshot["instruction"] == "call exec_wait; do not re-run"
        elapsed: object = snapshot["elapsed_s"]
        assert isinstance(elapsed, (int, float))
        assert float(elapsed) >= (poll_index + 1) * poll_s
        if harness.elapsed_s >= (pulses + 1) * _E2E_OUTPUT_PULSE_EVERY_S:
            pulses += 1
            last_line = f"[e2e] case_{pulses:03d} passed"
            first_line = first_line or last_line
            harness.stdout_pipe.emit(last_line)
            assert harness.stdout_pipe.wait_acked(pulses, _PIPE_ACK_TIMEOUT_S) is True
    return pulses, first_line, last_line


def test_exec_job_e2e_quiet_waiting_survives_45_minutes_at_production_defaults() -> None:
    cfg = default_liveness_config()
    assert cfg.evidence_window_s == td.EXEC_LIVENESS_EVIDENCE_WINDOW_S
    assert cfg.awake_per_s_inactive == td.EXEC_AWAKE_PER_S_INACTIVE
    assert cfg.awake_per_s_active == td.EXEC_AWAKE_PER_S_ACTIVE
    assert cfg.stall_theta == td.EXEC_STALL_THETA
    assert _E2E_TOTAL_S > td.EXEC_NO_PROGRESS_WINDOW_S
    assert _E2E_TOTAL_S < td.EXEC_JOB_CEILING_S
    assert _E2E_OUTPUT_PULSE_EVERY_S < td.EXEC_NO_PROGRESS_WINDOW_S

    harness = _E2EQuietWaitHarness(liveness_config=cfg)
    proc, _sampler, job_id = harness.start_via_bridge(
        command="e2e_suite",
        args=["--headed"],
    )
    pulses, first_line, last_line = _poll_quiet_waiting_to_end(harness, job_id)

    assert harness.elapsed_s == pytest.approx(_E2E_TOTAL_S)
    assert pulses == int(_E2E_TOTAL_S // _E2E_OUTPUT_PULSE_EVERY_S)

    proc._returncode = 0
    harness.stdout_pipe.close()
    assert harness.stdout_pipe.wait_closed(_PIPE_EOF_TIMEOUT_S) is True
    final_res = harness.bridge.dispatch(
        EXEC_WAIT_TOOL,
        {"job_id": job_id, "wait_ms": 1},
        host_session=harness.session,
        workspace=harness.workspace,
    )
    assert isinstance(final_res, ToolResult)
    assert final_res.is_error is False
    final_payload = _parse_payload(final_res)
    assert final_payload["job_id"] == job_id
    assert final_payload["running"] is False
    assert final_payload["returncode"] == 0

    output_obj: object = final_payload["output"]
    assert isinstance(output_obj, dict)
    final_output: dict[str, object] = {str(k): v for k, v in output_obj.items()}
    stdout_text: object = final_output["stdout"]
    assert isinstance(stdout_text, str)
    assert first_line in stdout_text and last_line in stdout_text
    assert final_output["stderr"] == "" and final_output["truncated"] is False
    assert final_output["spill_path"] is None

    final_result = harness.registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    assert isinstance(final_result, JobResult)
    assert final_result.kill_cause is None
    assert final_result.cancelled is False
    assert bool(proc._terminated) is False
    assert final_result.elapsed_s >= _E2E_TOTAL_S
    final_verdict = final_result.verdict
    assert final_verdict is not None and final_verdict.pressure == pytest.approx(0.0)
