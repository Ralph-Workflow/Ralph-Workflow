"""Tests for request-scoped MCP exec progress notifications (§3c / WU-5).

Verifies SSE streaming of `notifications/progress` frames when `progressToken`
is provided, token propagation for exec-family tools and exec_wait, absence
of progress frames when no token is supplied, and fail-safe subscription teardown.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, NamedTuple

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._json_rpc_response import JsonRpcResponse
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.server.exec_sse_streaming import (
    exec_sse_streaming_post,
    extract_job_id,
    extract_progress_token,
)
from ralph.mcp.tools._exec_jobs import ExecJobRegistry, ProgressSubscription
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

if TYPE_CHECKING:
    from collections.abc import Callable

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


class _CtxSwitchesNamedTuple(NamedTuple):
    voluntary: int = 0
    involuntary: int = 0


CtxSwitchesNamedTuple = _CtxSwitchesNamedTuple


class _CpuTimesNamedTuple(NamedTuple):
    user: float = 0.0
    system: float = 0.0


CpuTimesNamedTuple = _CpuTimesNamedTuple


class _IoCountersNamedTuple(NamedTuple):
    read_bytes: int = 0
    write_bytes: int = 0


IoCountersNamedTuple = _IoCountersNamedTuple


class _FakeSamplerProc:
    """Mock process tree node satisfying LivenessProc."""

    def __init__(self, pid: int = 123) -> None:
        self._pid = pid
        self._children: list[_FakeSamplerProc] = []
        self.user_cpu = 0.0
        self.system_cpu = 0.0
        self.read_bytes = 0
        self.write_bytes = 0
        self.voluntary_ctx = 0
        self.creation_time = 1000.0

    @property
    def pid(self) -> int:
        return self._pid

    def children(self, recursive: bool = False) -> Sequence[LivenessProc]:
        del recursive
        return self._children

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
    """Test environment bundle with deterministic FakeClock and ExecJobRegistry."""

    def __init__(self) -> None:
        self.clock = FakeClock(1000.0)
        self.pids = itertools.count(100)
        self.procs: dict[int, FakePopen] = {}
        self.sampler_procs: dict[int, FakeSamplerProc] = {}
        self.psutil_module = FakePsutilModule(self.sampler_procs)

        def sync_factory(command: Sequence[str], opts: SpawnOptions) -> _SyncProcessLike:
            del command, opts
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


def _extract_dict(obj: object) -> dict[str, object]:
    """Helper to extract a typed dict[str, object] from an object without Any leakage."""
    assert isinstance(obj, dict)
    out: dict[str, object] = {}
    for k, v in obj.items():
        val: object = v
        out[str(k)] = val
    return out


def _parse_sse_frames(raw_frames: list[bytes]) -> list[dict[str, object]]:
    """Decode raw SSE frames into parsed JSON-RPC payload dictionaries."""
    frames: list[dict[str, object]] = []
    for raw in raw_frames:
        text = raw.decode("utf-8")
        for chunk in text.split("\r\n\r\n"):
            trimmed = chunk.strip()
            if not trimmed:
                continue
            for line in trimmed.splitlines():
                if line.startswith("data: "):
                    raw_parsed: object = json.loads(line[len("data: ") :])
                    if isinstance(raw_parsed, dict):
                        frames.append(_extract_dict(raw_parsed))
    return frames


def test_extract_progress_token_and_job_id() -> None:
    """Verifies token extraction across spec and alternate param placements."""
    req1 = JsonRpcRequest("2.0", "tools/call", {"_meta": {"progressToken": "tok-1"}})
    assert extract_progress_token(req1) == "tok-1"

    req2 = JsonRpcRequest("2.0", "tools/call", {"progressToken": "tok-2"})
    assert extract_progress_token(req2) == "tok-2"

    req3 = JsonRpcRequest("2.0", "tools/call", {"arguments": {"_meta": {"progressToken": "tok-3"}}})
    assert extract_progress_token(req3) == "tok-3"

    req4 = JsonRpcRequest("2.0", "tools/call", {"arguments": {"progressToken": "tok-4"}})
    assert extract_progress_token(req4) == "tok-4"

    req_int = JsonRpcRequest("2.0", "tools/call", {"progressToken": 42})
    assert extract_progress_token(req_int) == 42

    req_none = JsonRpcRequest("2.0", "tools/call", {"arguments": {"command": "ls"}})
    assert extract_progress_token(req_none) is None

    req_job = JsonRpcRequest("2.0", "tools/call", {"arguments": {"job_id": "job_123"}})
    assert extract_job_id(req_job) == "job_123"


def test_progress_frames_emitted_when_token_present() -> None:
    """A token-bearing request emits notifications/progress on each liveness sample."""
    env = _TestEnv()
    session = AgentSession("sess-1", "run-1", "http://localhost", {"ProcessExecBounded"})

    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        params={
            "name": "exec",
            "arguments": {"command": "long_task"},
            "_meta": {"progressToken": "tok-progress-1"},
        },
        msg_id=101,
    )

    written: list[bytes] = []

    def handle_request(
        req: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse, ServerState]:
        # Emit an output chunk
        sink = session.current_thread_tool_output_sink()
        if sink is not None:
            sink({"tool": "exec", "stream": "stdout", "text": "task started"})

        # Step the job registry, which triggers liveness progress dispatch
        job_id = env.registry.start(["cmd"], capability="ProcessExecBounded")
        env.clock.advance(1.5)
        env.registry.step_job(job_id, dt=1.5)

        return (
            JsonRpcResponse(
                jsonrpc="2.0",
                result={"content": [{"type": "text", "text": "done"}]},
                msg_id=req.msg_id,
            ),
            state,
        )

    exec_sse_streaming_post(
        request,
        session,
        handle_request,
        ServerState.RUNNING,
        write_frame=written.append,
        registry=env.registry,
    )

    frames = _parse_sse_frames(written)
    assert len(frames) >= 3

    # Frame 1: notifications/message chunk
    msg_frames = [f for f in frames if f.get("method") == "notifications/message"]
    assert len(msg_frames) >= 1
    assert msg_frames[0].get("params") == {
        "tool": "exec",
        "stream": "stdout",
        "text": "task started",
    }

    # Frame 2: notifications/progress frame
    prog_frames = [f for f in frames if f.get("method") == "notifications/progress"]
    assert len(prog_frames) >= 1
    prog_params = _extract_dict(prog_frames[0].get("params"))
    assert prog_params.get("progressToken") == "tok-progress-1"
    assert isinstance(prog_params.get("progress"), (int, float))

    # Frame 3: final JSON-RPC response
    final_frames = [f for f in frames if f.get("id") == 101]
    assert len(final_frames) == 1
    final_res = _extract_dict(final_frames[0].get("result"))
    assert final_res == {"content": [{"type": "text", "text": "done"}]}


@pytest.mark.parametrize("tool_name", ["exec", "unsafe_exec", "raw_exec"])
def test_token_bearing_exec_family_tools(tool_name: str) -> None:
    """Each exec-family tool correctly streams progress when supplied a token."""
    env = _TestEnv()
    session = AgentSession("sess-1", "run-1", "http://localhost", {"ProcessExecBounded"})

    token = f"tok-{tool_name}-99"
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        params={
            "name": tool_name,
            "progressToken": token,
            "arguments": {"command": "tool_cmd"},
        },
        msg_id=202,
    )

    written: list[bytes] = []

    def handle_request(
        req: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse, ServerState]:
        job_id = env.registry.start(["cmd"], capability="ProcessExecBounded")
        env.clock.advance(2.0)
        env.registry.step_job(job_id, dt=2.0)
        return (
            JsonRpcResponse(jsonrpc="2.0", result={"status": "ok"}, msg_id=req.msg_id),
            state,
        )

    exec_sse_streaming_post(
        request,
        session,
        handle_request,
        ServerState.RUNNING,
        write_frame=written.append,
        registry=env.registry,
    )

    frames = _parse_sse_frames(written)
    prog_frames = [f for f in frames if f.get("method") == "notifications/progress"]
    assert len(prog_frames) >= 1
    prog_params = _extract_dict(prog_frames[0].get("params"))
    assert prog_params.get("progressToken") == token


def test_exec_wait_with_distinct_progress_token() -> None:
    """A subsequent exec_wait receives progress frames stamped with its own distinct token."""
    env = _TestEnv()
    session = AgentSession("sess-1", "run-1", "http://localhost", {"ProcessExecBounded"})

    # Start a background job
    job_id = env.registry.start(["long_running"], capability="ProcessExecBounded")

    # 1. Initial exec call with tok-initial
    req1 = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        params={
            "name": "exec",
            "progressToken": "tok-initial",
            "arguments": {"command": "long_running"},
        },
        msg_id=1,
    )
    written1: list[bytes] = []

    def handle_req1(req: JsonRpcRequest, state: ServerState) -> tuple[JsonRpcResponse, ServerState]:
        env.clock.advance(1.0)
        env.registry.step_job(job_id, dt=1.0)
        return (
            JsonRpcResponse(jsonrpc="2.0", result={"job_id": job_id}, msg_id=req.msg_id),
            state,
        )

    exec_sse_streaming_post(
        req1,
        session,
        handle_req1,
        ServerState.RUNNING,
        write_frame=written1.append,
        registry=env.registry,
    )

    frames1 = _parse_sse_frames(written1)
    prog1 = [f for f in frames1 if f.get("method") == "notifications/progress"]
    assert len(prog1) >= 1
    p1_params = _extract_dict(prog1[0].get("params"))
    assert p1_params.get("progressToken") == "tok-initial"

    # 2. Subsequent exec_wait call with DISTINCT tok-wait
    req2 = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        params={
            "name": "exec_wait",
            "_meta": {"progressToken": "tok-wait"},
            "arguments": {"job_id": job_id},
        },
        msg_id=2,
    )
    written2: list[bytes] = []

    def handle_req2(req: JsonRpcRequest, state: ServerState) -> tuple[JsonRpcResponse, ServerState]:
        env.clock.advance(1.0)
        env.registry.step_job(job_id, dt=1.0)
        return (
            JsonRpcResponse(jsonrpc="2.0", result={"running": True}, msg_id=req.msg_id),
            state,
        )

    exec_sse_streaming_post(
        req2,
        session,
        handle_req2,
        ServerState.RUNNING,
        write_frame=written2.append,
        registry=env.registry,
    )

    frames2 = _parse_sse_frames(written2)
    prog2 = [f for f in frames2 if f.get("method") == "notifications/progress"]
    assert len(prog2) >= 1
    p2_params = _extract_dict(prog2[0].get("params"))
    assert p2_params.get("progressToken") == "tok-wait"

    # Crucial assertion: no frames in req2 carried tok-initial, and no frames in req1 carried tok-wait
    for f in prog2:
        params = _extract_dict(f.get("params"))
        assert params.get("progressToken") != "tok-initial"


def test_no_progress_frames_emitted_when_token_absent() -> None:
    """When progressToken is absent, behavior is unchanged: no progress frames are emitted."""
    env = _TestEnv()
    session = AgentSession("sess-1", "run-1", "http://localhost", {"ProcessExecBounded"})

    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        params={
            "name": "exec",
            "arguments": {"command": "simple"},
        },
        msg_id=303,
    )

    written: list[bytes] = []

    def handle_request(
        req: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse, ServerState]:
        sink = session.current_thread_tool_output_sink()
        if sink is not None:
            sink({"tool": "exec", "stream": "combined", "text": "some output"})

        job_id = env.registry.start(["cmd"], capability="ProcessExecBounded")
        env.clock.advance(1.0)
        env.registry.step_job(job_id, dt=1.0)
        return (
            JsonRpcResponse(jsonrpc="2.0", result={"status": "ok"}, msg_id=req.msg_id),
            state,
        )

    exec_sse_streaming_post(
        request,
        session,
        handle_request,
        ServerState.RUNNING,
        write_frame=written.append,
        registry=env.registry,
    )

    frames = _parse_sse_frames(written)
    # Output chunk notifications/message must be present
    msg_frames = [f for f in frames if f.get("method") == "notifications/message"]
    assert len(msg_frames) == 1

    # notifications/progress MUST NOT be present
    prog_frames = [f for f in frames if f.get("method") == "notifications/progress"]
    assert len(prog_frames) == 0

    # Final response is present
    assert any(f.get("id") == 303 for f in frames)


def test_teardown_prevents_stale_token_emission() -> None:
    """Subscription teardown ensures completed requests never receive further progress frames."""
    env = _TestEnv()
    session = AgentSession("sess-1", "run-1", "http://localhost", {"ProcessExecBounded"})

    captured_sub: list[ProgressSubscription] = []
    job_id = env.registry.start(["job"], capability="ProcessExecBounded")

    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        params={
            "name": "exec",
            "_meta": {"progressToken": "tok-ephemeral"},
            "arguments": {"job_id": job_id},
        },
        msg_id=404,
    )

    written: list[bytes] = []

    def handle_request(
        req: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse, ServerState]:
        captured_sub.extend(env.registry._subscriptions.values())
        env.clock.advance(1.0)
        env.registry.step_job(job_id, dt=1.0)
        return (
            JsonRpcResponse(jsonrpc="2.0", result={"done": True}, msg_id=req.msg_id),
            state,
        )

    exec_sse_streaming_post(
        request,
        session,
        handle_request,
        ServerState.RUNNING,
        write_frame=written.append,
        registry=env.registry,
    )

    # During request execution, 1 progress frame was recorded
    frames = _parse_sse_frames(written)
    prog_frames = [f for f in frames if f.get("method") == "notifications/progress"]
    assert len(prog_frames) == 1
    assert len(captured_sub) == 1
    sub = captured_sub[0]

    # After request completion:
    # 1. Registry no longer holds the subscription
    assert sub.request_id not in env.registry._subscriptions

    # 2. Subsequent job steps do NOT dispatch frames to the finished request's writer
    before_len = len(written)
    env.clock.advance(1.0)
    env.registry.step_job(job_id, dt=1.0)
    assert len(written) == before_len

    # 3. Invoking the stale sink directly drops the frame because active_flag is False
    sub.sink({"progressToken": "tok-ephemeral", "progress": 10.0})
    assert len(written) == before_len
