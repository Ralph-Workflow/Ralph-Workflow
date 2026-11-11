from __future__ import annotations

import io
import itertools
import threading
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools import ExecJobRegistry, JobResult
from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.testing.fake_process import FakePsutil, ProcessState, ProcessStreams
from ralph.timeout_defaults import EXEC_JOB_ABANDON_S, EXEC_SOFT_REPLY_BUDGET_MS
from tests.test_exec_jobs import FakePsutilModule, FakeSamplerProc, _ControllableFakePopen

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ralph.process.manager import SpawnOptions, SyncProcessLike

_CAP = "ProcessExecBounded"
_CAPTURE_BYTES = 65_536
_MARKER = "\n... [truncated] ...\n"
_HANG_CAP_S = 3.0


class _HeldPipe(io.BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__()
        self._payload = payload
        self._io_lock = threading.Lock()
        self.parked = threading.Event()
        self.release = threading.Event()
        self.closed_by_owner = threading.Event()

    def read1(self, size: int | None = -1, /) -> bytes:
        with self._io_lock:
            if self._payload:
                cut = len(self._payload) if size is None or size < 0 else size
                chunk, self._payload = self._payload[:cut], self._payload[cut:]
                return chunk
            self.parked.set()
            self.release.wait(timeout=_HANG_CAP_S)
            return b""

    def close(self) -> None:
        with self._io_lock:
            super().close()
        self.closed_by_owner.set()


class _WaitAwareClock:
    def __init__(self, initial: float = 1000.0) -> None:
        self.now = initial
        self.waiter: threading.Thread | None = None
        self.read_by_waiter = threading.Event()

    def __call__(self) -> float:
        if threading.current_thread() is self.waiter:
            self.read_by_waiter.set()
        return self.now


class _Env:
    __test__ = False

    def __init__(
        self,
        *,
        stdout: io.BytesIO | None = None,
        clock: Callable[[], float] | None = None,
        spill_dir: Path | None = None,
        reader_drain_timeout_s: float = 1.0,
    ) -> None:
        self.procs: list[_ControllableFakePopen] = []
        pids = itertools.count(100)
        sampler_procs: dict[int, FakeSamplerProc] = {}

        def sync_factory(command: Sequence[str], opts: SpawnOptions) -> SyncProcessLike:
            pid = next(pids)
            proc = _ControllableFakePopen(
                pid=pid,
                state=ProcessState(returncode=None),
                streams=ProcessStreams(stdout=stdout),
            )
            self.procs.append(proc)
            sampler_procs[pid] = FakeSamplerProc(pid=pid)
            return proc

        pm = ProcessManager(
            policy=ProcessManagerPolicy(default_grace_period_s=0.0),
            sync_process_factory=sync_factory,
            psutil=FakePsutil(),
        )
        self.registry = ExecJobRegistry(
            process_manager=pm,
            clock=clock if clock is not None else _WaitAwareClock(),
            psutil_module=FakePsutilModule(sampler_procs),
            auto_register_runtime_hooks=False,
            background_sampler=False,
            spill_dir=spill_dir,
            reader_drain_timeout_s=reader_drain_timeout_s,
        )


def _finish_root(env: _Env, job_id: str) -> None:
    env.procs[-1].finish(0)
    env.registry.step_job(job_id)


def _cancel(env: _Env, job_id: str) -> None:
    env.registry.cancel(job_id, capability=_CAP)


@pytest.mark.timeout_seconds(_HANG_CAP_S + 2.0)
@pytest.mark.parametrize("stop", [_finish_root, _cancel], ids=["root_exit", "cancel"])
def test_detached_grandchild_holding_pipes_does_not_block_job_finish(
    stop: Callable[[_Env, str], None],
) -> None:
    stdout = _HeldPipe(b"parent done\n")
    env = _Env(stdout=stdout, reader_drain_timeout_s=0.05)
    job_id = env.registry.start(
        ["sh", "-c", "setsid sleep 600 & echo parent done"], capability=_CAP
    )
    assert stdout.parked.wait(timeout=_HANG_CAP_S), "reader never drained the payload"

    stopper = threading.Thread(target=stop, args=(env, job_id), daemon=True)
    stopper.start()
    try:
        result = env.registry.wait(job_id, wait_ms=int(_HANG_CAP_S * 1000) // 2, capability=_CAP)
        assert isinstance(result, JobResult)
        assert "parent done" in str(result.output["stdout"])
        assert env.registry.is_running(job_id) is False
        published = env.registry.last_finished_result()
        assert published is not None
        assert published.job_id == job_id
    finally:
        stdout.release.set()
        stopper.join(timeout=_HANG_CAP_S)
    assert not stopper.is_alive()
    assert stdout.closed_by_owner.wait(timeout=_HANG_CAP_S)


def _numbered_lines(total_bytes: int) -> bytes:
    lines = b"".join(b"line %05d\n" % i for i in range(total_bytes // 11 + 1))
    return lines[:total_bytes]


@pytest.mark.parametrize(
    "total_bytes",
    [110_000, 2 * _CAPTURE_BYTES, 2 * _CAPTURE_BYTES + 10, 300_000],
    ids=["between_one_and_two_buffers", "exactly_two_buffers", "just_over", "far_over"],
)
def test_captured_output_is_never_duplicated(total_bytes: int, tmp_path: Path) -> None:
    data = _numbered_lines(total_bytes)
    env = _Env(stdout=io.BytesIO(data), spill_dir=tmp_path)
    job_id = env.registry.start(["emit"], capability=_CAP)
    _finish_root(env, job_id)

    result = env.registry.wait(job_id, wait_ms=0, capability=_CAP)
    assert isinstance(result, JobResult)
    stdout = str(result.output["stdout"])
    assert result.output["stdout_bytes"] == total_bytes
    if total_bytes <= 2 * _CAPTURE_BYTES:
        assert stdout == data.decode()
        assert result.output["truncated"] is False
    else:
        expected = data[:_CAPTURE_BYTES].decode() + _MARKER + data[-_CAPTURE_BYTES:].decode()
        assert stdout == expected
        assert result.output["truncated"] is True


@pytest.mark.timeout_seconds(_HANG_CAP_S)
def test_abandon_clock_restarts_when_wait_returns() -> None:
    clock = _WaitAwareClock()
    env = _Env(clock=clock)
    job_id = env.registry.start(["long_build"], timeout_ms=3_600_000, capability=_CAP)

    def soft_reply_wait() -> None:
        env.registry.wait(job_id, wait_ms=300, capability=_CAP)

    waiter = threading.Thread(target=soft_reply_wait)
    clock.waiter = waiter
    waiter.start()
    assert clock.read_by_waiter.wait(timeout=_HANG_CAP_S)
    clock.now += EXEC_SOFT_REPLY_BUDGET_MS / 1000.0
    waiter.join(timeout=_HANG_CAP_S)
    assert not waiter.is_alive()

    clock.now += EXEC_JOB_ABANDON_S - 1.0
    env.registry.step_job(job_id, dt=1.0)
    assert env.registry.is_running(job_id), "job abandoned before the window since the reply"

    clock.now += 2.0
    env.registry.step_job(job_id, dt=1.0)
    result = env.registry.wait(job_id, wait_ms=0, capability=_CAP)
    assert isinstance(result, JobResult)
    assert result.kill_cause == "abandoned"
