from __future__ import annotations

import io
import math
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools._exec_jobs import JobResult, JobSnapshot
from tests.test_exec_jobs import _TestEnv

if TYPE_CHECKING:
    from ralph.process.manager._liveness_assessor import LivenessVerdict

_CAPABILITY = "ProcessExecBounded"
_DT = 1.0
_STALL_WINDOW_S = 5.0
_EVIDENCE_WINDOW_S = 30.0
_COLLAPSE_SECOND = int(_EVIDENCE_WINDOW_S) + 1
_PRESSURE_PER_STEP_S = 0.6


def _emit(env: _TestEnv, job_id: str, payload: bytes) -> None:
    job = env.registry._running_jobs[job_id]
    stream = io.BufferedReader(io.BytesIO(payload))
    env.registry._read_stream(job, stream, True)


def _startup_burst(count: int) -> bytes:
    return b"".join(f"build-step-{i}\n".encode() for i in range(count))


def test_historic_startup_novelty_cannot_mask_repetitive_output_stall() -> None:
    env = _TestEnv()
    job_id = env.registry.start(
        ["noisy_build_then_loop"],
        timeout_ms=int(_STALL_WINDOW_S * 1000),
        capability=_CAPABILITY,
    )
    root_proc = next(iter(env.procs.values()))
    theta = env.registry._liveness_config.stall_theta

    _emit(env, job_id, _startup_burst(900))

    by_second: dict[int, LivenessVerdict] = {}
    for second in range(1, 90):
        env.clock.advance(_DT)
        _emit(env, job_id, b"retry\n")
        env.registry.wait(job_id, wait_ms=0, capability=_CAPABILITY)
        env.registry.step_job(job_id, dt=_DT)
        if job_id not in env.registry._running_jobs:
            break
        snapshot = env.registry.wait(job_id, wait_ms=0, capability=_CAPABILITY)
        assert isinstance(snapshot, JobSnapshot)
        assert snapshot.verdict is not None
        by_second[second] = snapshot.verdict

    pre_collapse = by_second[int(_EVIDENCE_WINDOW_S)]
    assert pre_collapse.signals["output_novelty"] == pytest.approx(901 / 930)
    assert pre_collapse.strongest_rule == "R1"
    assert pre_collapse.verdict_kind == "working"
    assert pre_collapse.score == 1.0
    assert pre_collapse.pressure == 0.0

    collapse = by_second[_COLLAPSE_SECOND]
    assert collapse.signals["output_novelty"] == pytest.approx(1 / 31)
    assert collapse.strongest_rule == "R6"
    assert collapse.verdict_kind == "working"
    assert collapse.score == pytest.approx(0.2)
    assert collapse.score < theta
    assert collapse.pressure == pytest.approx(_PRESSURE_PER_STEP_S)

    result = env.registry.wait(job_id, wait_ms=0, capability=_CAPABILITY)
    assert isinstance(result, JobResult)
    assert result.kill_cause == "stalled"
    assert result.cancelled is False
    assert result.returncode is None
    assert result.verdict is not None
    assert result.verdict.strongest_rule == "R6"
    assert result.verdict.verdict_kind == "working"
    assert result.verdict.score == pytest.approx(0.2)
    assert result.verdict.pressure >= _STALL_WINDOW_S
    steps_to_kill = math.ceil(_STALL_WINDOW_S / _PRESSURE_PER_STEP_S)
    assert result.elapsed_s == pytest.approx(float(_COLLAPSE_SECOND + steps_to_kill - 1), abs=1e-9)

    assert root_proc._terminated is True
    assert root_proc.pid in env.pm.orphans_cleaned
    assert job_id not in env.registry._running_jobs
    assert env.registry._finished_results[-1] is result


def test_novelty_unknown_when_current_window_holds_fewer_than_twenty_lines() -> None:
    env = _TestEnv()
    job_id = env.registry.start(
        ["noisy_build_then_sparse"],
        timeout_ms=90_000,
        capability=_CAPABILITY,
    )

    _emit(env, job_id, _startup_burst(900))

    for _ in range(_COLLAPSE_SECOND):
        env.clock.advance(_DT)
        env.registry.wait(job_id, wait_ms=0, capability=_CAPABILITY)
        env.registry.step_job(job_id, dt=_DT)

    aged = env.registry.wait(job_id, wait_ms=0, capability=_CAPABILITY)
    assert isinstance(aged, JobSnapshot)
    assert aged.running is True
    aged_verdict = aged.verdict
    assert aged_verdict is not None
    assert aged_verdict.signals["seconds_since_output"] == pytest.approx(float(_COLLAPSE_SECOND))
    assert aged_verdict.signals["output_novelty"] is None
    assert aged_verdict.verdict_kind == "working"

    env.clock.advance(_DT)
    _emit(env, job_id, b"".join(b"retry\n" for _ in range(5)))
    env.registry.wait(job_id, wait_ms=0, capability=_CAPABILITY)
    env.registry.step_job(job_id, dt=_DT)

    sparse = env.registry.wait(job_id, wait_ms=0, capability=_CAPABILITY)
    assert isinstance(sparse, JobSnapshot)
    assert sparse.running is True
    sparse_verdict = sparse.verdict
    assert sparse_verdict is not None
    assert sparse_verdict.signals["seconds_since_output"] == pytest.approx(0.0)
    assert sparse_verdict.signals["output_novelty"] is None
    assert sparse_verdict.strongest_rule == "R1"
    assert sparse_verdict.verdict_kind == "working"
    assert sparse_verdict.score == 1.0

    for _ in range(3):
        env.clock.advance(_DT)
        _emit(env, job_id, b"".join(b"retry\n" for _ in range(5)))
        env.registry.wait(job_id, wait_ms=0, capability=_CAPABILITY)
        env.registry.step_job(job_id, dt=_DT)

    gated = env.registry.wait(job_id, wait_ms=0, capability=_CAPABILITY)
    assert isinstance(gated, JobSnapshot)
    assert gated.running is True
    gated_verdict = gated.verdict
    assert gated_verdict is not None
    assert gated_verdict.signals["output_novelty"] == pytest.approx(1 / 20)
    assert gated_verdict.strongest_rule == "R6"
    assert gated_verdict.score == pytest.approx(0.2)
