"""WU-1 regression: output novelty must be measured INSIDE the evidence window.

Production-seam evidence for the defect this file locks:

``_ActiveJob.output_lines`` used to be an UN-timestamped FIFO of the last
1000 output strings, and ``TreeSampler`` took ``distinct / total`` over
every string it was handed. Nine hundred DISTINCT build lines therefore
pinned novelty at ~0.97 forever, so ``R6`` (Repetitive) never fired, the
literal-Sugeno score stayed above ``theta = 0.5``, stall pressure stayed at
0.0, and a command that had degenerated into printing ``retry`` once per
second was classified ``working`` forever and was NEVER killed as
``stalled``.

The seam now carries timestamped ``OutputEvidence`` records and the sampler
prunes records older than ``config.evidence_window_s`` before taking the
ratio. Both tests below drive the REAL ``ExecJobRegistry`` (fake clock, fake
process manager, no threads, no sleeps, no subprocesses); output enters
through the REAL ``ExecJobRegistry._read_stream`` reader, and the sampled
window, the assessed verdict, the pressure integration and the ``stalled``
kill all come from production code.
"""

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
#: ``timeout_ms`` IS the §3b stall window; 5 s keeps the regression fast.
_STALL_WINDOW_S = 5.0
#: ``_make_test_config``'s ``evidence_window_s``.
_EVIDENCE_WINDOW_S = 30.0
#: First second at which the 900 historic lines fall outside the window.
_COLLAPSE_SECOND = int(_EVIDENCE_WINDOW_S) + 1
#: R6 alone (z=0.2) against theta=0.5 at dt=1.0: (0.5 - 0.2) / 0.5.
_PRESSURE_PER_STEP_S = 0.6


def _emit(env: _TestEnv, job_id: str, payload: bytes) -> None:
    """Push ``payload`` through the production stdout reader for ``job_id``.

    ``ExecJobRegistry._read_stream`` is the exact callable the reader
    thread runs (``partial(self._read_stream, active, handle.stdout, True)``),
    so the line splitting, the ``last_output_at`` update and the timestamped
    evidence append are all production code. Nothing here writes job state.
    """
    job = env.registry._running_jobs[job_id]
    stream = io.BufferedReader(io.BytesIO(payload))
    env.registry._read_stream(job, stream, True)


def _startup_burst(count: int) -> bytes:
    """``count`` DISTINCT output lines as one stdout chunk."""
    return b"".join(f"build-step-{i}\n".encode() for i in range(count))


def test_historic_startup_novelty_cannot_mask_repetitive_output_stall() -> None:
    """A burst of distinct startup lines must stop counting once it ages out.

    Drives the real registry: 900 distinct build lines at t=1000, then the
    SAME line (``retry``) once per second. While the burst is inside the
    30 s evidence window the job reads as productive (R1, score 1.0, zero
    pressure). One second after the burst ages out, novelty is 1/31 — fully
    repetitive — R6 takes over, the score drops to 0.2 (below ``theta``),
    pressure accumulates at 0.6/s, and the job is killed with
    ``kill_cause == "stalled"``.
    """
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
        # From here on the command prints one repeated line every second.
        _emit(env, job_id, b"retry\n")
        # Refresh the wait stamp exactly as the real client poll does, so the
        # abandonment branch stays unreachable and only the stall branch can fire.
        env.registry.wait(job_id, wait_ms=0, capability=_CAPABILITY)
        env.registry.step_job(job_id, dt=_DT)
        if job_id not in env.registry._running_jobs:
            break
        snapshot = env.registry.wait(job_id, wait_ms=0, capability=_CAPABILITY)
        assert isinstance(snapshot, JobSnapshot)
        assert snapshot.verdict is not None
        by_second[second] = snapshot.verdict

    # Pre-collapse: the burst is still inside the window, so 901 distinct
    # lines out of 930 total keep novelty high and the job is classified
    # productive with zero stall pressure.
    pre_collapse = by_second[int(_EVIDENCE_WINDOW_S)]
    assert pre_collapse.signals["output_novelty"] == pytest.approx(901 / 930)
    assert pre_collapse.strongest_rule == "R1"
    assert pre_collapse.verdict_kind == "working"
    assert pre_collapse.score == 1.0
    assert pre_collapse.pressure == 0.0

    # Collapse: the SAME retained FIFO now yields only the 31 in-window
    # "retry" lines, so novelty is 1/31 — inside the repetitive band — and
    # R6 (z = 0.2) replaces R1 (z = 1.0).
    collapse = by_second[_COLLAPSE_SECOND]
    assert collapse.signals["output_novelty"] == pytest.approx(1 / 31)
    assert collapse.strongest_rule == "R6"
    assert collapse.verdict_kind == "working"
    assert collapse.score == pytest.approx(0.2)
    assert collapse.score < theta
    # One integration step at dt=1.0: (theta - 0.2) / theta.
    assert collapse.pressure == pytest.approx(_PRESSURE_PER_STEP_S)

    result = env.registry.wait(job_id, wait_ms=0, capability=_CAPABILITY)
    assert isinstance(result, JobResult)
    assert result.kill_cause == "stalled"
    assert result.cancelled is False
    assert result.returncode is None
    assert result.verdict is not None
    assert result.verdict.strongest_rule == "R6"
    # R6 is not one of the assessor's suspicious rules, so the classification
    # stays "working"; the kill comes from ``StallIntegrator.should_kill``,
    # NOT from the verdict kind.
    assert result.verdict.verdict_kind == "working"
    assert result.verdict.score == pytest.approx(0.2)
    # ``should_kill(stall_window_s)`` is P >= 5.0. Pressure only starts
    # building at the collapse second, at 0.6 per dt=1.0 step, so the kill
    # cannot land before it and lands exactly 9 integration steps later.
    assert result.verdict.pressure >= _STALL_WINDOW_S
    steps_to_kill = math.ceil(_STALL_WINDOW_S / _PRESSURE_PER_STEP_S)
    assert result.elapsed_s == pytest.approx(float(_COLLAPSE_SECOND + steps_to_kill - 1), abs=1e-9)

    # CLEANUP EVIDENCE (root-only scope — the fake process tree has no
    # descendants, so this does NOT prove descendant reaping).
    assert root_proc._terminated is True
    assert root_proc.pid in env.pm.orphans_cleaned
    assert job_id not in env.registry._running_jobs
    assert env.registry._finished_results[-1] is result


def test_novelty_unknown_when_current_window_holds_fewer_than_twenty_lines() -> None:
    """The < 20-line novelty gate applies to the CURRENT window, not the FIFO.

    Same 900-line historic burst, but the command then goes quiet long
    enough for the burst to age out and emits only a handful of lines.
    With fewer than 20 lines inside the window, novelty stays unknown
    (``None``), so ``Repetitive`` is 0 and R1 keeps scoring the job as
    productive — the fail-closed "unknown" semantics of D5, not a stall.
    """
    env = _TestEnv()
    job_id = env.registry.start(
        ["noisy_build_then_sparse"],
        timeout_ms=90_000,
        capability=_CAPABILITY,
    )

    _emit(env, job_id, _startup_burst(900))

    # Quiet for long enough that the burst ages out (t=1000 + 31 s is the
    # first sample whose cutoff excludes it) while the job is still running
    # — the 90 s stall window keeps the stall branch off this trace.
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
    # The burst is out of the window, so nothing is left to score novelty on.
    assert aged_verdict.signals["output_novelty"] is None
    assert aged_verdict.verdict_kind == "working"

    # Five in-window lines: below the 20-line gate, so novelty is STILL
    # unknown even though output is fresh (``seconds_since_output == 0``).
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

    # Three more steps take the window to exactly 20 identical lines: the
    # gate opens and novelty is the repetitive 1/20 = 0.05.
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
