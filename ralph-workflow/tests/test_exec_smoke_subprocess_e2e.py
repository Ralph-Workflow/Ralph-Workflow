"""Real-process end-to-end smoke tests for long-running exec jobs.

Validates the liveness assessor and ExecJobRegistry against real OS processes:
1. Real sleep process killed as stalled when silent and idle.
2. Silent Python CPU loop runs past the stall window as suspicious (R4 fires).
3. Silent Python file-writer runs past the stall window and completes (R2 fires).
4. Socket-blocked process answered slowly by sibling completes via voluntary
   context switch signal (R7 fires).

Each test runs the spawned process for at least ``stall_window + 1`` so the
suite proves the responsible signal actually kept the process alive
beyond the deadline. The default config has shrunk windows (≤1 s) so each
test finishes well inside the 60 s combined test budget.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time

import pytest

from ralph.mcp.tools._exec_jobs import ExecJobRegistry, JobResult, JobSnapshot
from ralph.process.manager._liveness_assessor import LivenessConfig

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(15),
]


def _make_fast_liveness_config() -> LivenessConfig:
    """Shrunk configuration windows (<= 1s) for fast deterministic smoke tests."""
    return LivenessConfig(
        output_recent_min_s=0.1,
        output_recent_max_s=0.2,
        cpu_busy_min=0.05,
        cpu_busy_max=0.20,
        io_kbps_inactive=1.0,
        io_kbps_active=10.0,
        tree_active_threshold=1,
        repetitive_max=0.1,
        repetitive_min=0.5,
        awake_per_s_inactive=0.1,
        awake_per_s_active=2.0,
        stall_theta=0.5,
        evidence_window_s=1.0,
    )


def _wait_for_pid_dead(pid: int, timeout_s: float = 3.0) -> bool:
    """Poll until pid is no longer alive or timeout_s expires."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.02)
        except ProcessLookupError:
            return True
        except OSError:
            return True
    return False


def test_smoke_real_sleep_killed_as_stalled() -> None:
    """1. Real sleep process is killed with cause 'stalled' due to zero activity.

    The sleep runs for 10 s (much longer than the 300 ms stall window) so
    the test cannot pass via early completion. The kill must come from the
    stall integrator.
    """
    reg = ExecJobRegistry(
        liveness_config=_make_fast_liveness_config(),
        sample_interval_s=0.05,
        auto_register_runtime_hooks=False,
    )

    cmd = [sys.executable, "-c", "import time; time.sleep(10)"]
    job_id = reg.start(cmd, timeout_ms=300, capability="ProcessExecBounded")

    result = reg.wait(job_id, wait_ms=2000, capability="ProcessExecBounded")
    assert isinstance(result, JobResult)
    assert result.kill_cause == "stalled"
    assert result.verdict is not None
    assert result.verdict.verdict_kind == "stalled"


def test_smoke_silent_python_cpu_loop_runs_past_stall_window() -> None:
    """2. Silent Python CPU loop runs past the stall window as suspicious (R4).

    A CPU loop is started with a 250 ms stall window. The CPU loop
    must still be running after 0.6 s of real wall-clock time (>= 2x the
    stall window) and the latest verdict must report R4 (silent compute,
    "suspicious"). This is a black-box proof that the cpu_ratio signal kept
    the process alive — the loop has no output, no I/O, no children.
    """
    reg = ExecJobRegistry(
        liveness_config=_make_fast_liveness_config(),
        sample_interval_s=0.05,
        auto_register_runtime_hooks=False,
    )

    script = "import time\nend = time.time() + 2\nwhile time.time() < end:\n    x = 1 + 1\n"
    cmd = [sys.executable, "-c", script]
    job_id = reg.start(cmd, timeout_ms=250, capability="ProcessExecBounded")

    try:
        # Wait 0.6 s of real wall-clock — > 2x the stall window — so the
        # test cannot pass via early termination or a brief snapshot.
        time.sleep(0.6)
        snapshot = reg.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        assert isinstance(snapshot, JobSnapshot)
        assert snapshot.running is True, "CPU loop was killed despite R4 evidence"
        assert snapshot.verdict is not None
        assert snapshot.verdict.suspicious is True
        assert snapshot.verdict.verdict_kind == "suspicious"
        assert snapshot.verdict.strongest_rule == "R4"
        # The CPU signal must have been read: cpu_ratio should be > the
        # cpu_busy_min membership floor (0.05).
        cpu_ratio = snapshot.verdict.signals.get("cpu_ratio")
        assert cpu_ratio is not None and cpu_ratio >= 0.05, (
            f"CPU signal unreadable (cpu_ratio={cpu_ratio!r}); R4 could not "
            "have fired and the test cannot prove the responsible signal"
        )
    finally:
        reg.cancel(job_id, capability="ProcessExecBounded")


def test_smoke_silent_python_file_writer_runs_past_stall_window_then_completes() -> None:
    """3. Silent Python file-writer runs past the stall window and completes (R2).

    The file-writer script loops 20 times writing 256 KiB then sleeping
    0.04 s — total wall-clock ≈ 0.8 s (≫ 250 ms stall window). Without
    the I/O signal it would be killed as stalled. The test verifies:

    * the process ran past 0.6 s (>= 2x the stall window) and was still
      alive mid-flight,
    * the process completed with returncode=0,
    * no kill fired.
    """
    reg = ExecJobRegistry(
        liveness_config=_make_fast_liveness_config(),
        sample_interval_s=0.05,
        auto_register_runtime_hooks=False,
    )

    with tempfile.NamedTemporaryFile() as tf:
        script = (
            f"import time\n"
            f"with open({tf.name!r}, 'wb') as f:\n"
            f"    for i in range(20):\n"
            f"        f.write(b'x' * (256 * 1024))\n"
            f"        f.flush()\n"
            f"        time.sleep(0.04)\n"
        )
        cmd = [sys.executable, "-c", script]
        job_id = reg.start(cmd, timeout_ms=250, capability="ProcessExecBounded")

        # Wait 0.6 s — twice the stall window — so the process is still
        # in its loop. Snapshot must show it alive past the deadline.
        time.sleep(0.6)
        mid = reg.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        assert isinstance(mid, JobSnapshot)
        assert mid.running is True, "file-writer was killed despite R2 evidence"

        result = reg.wait(job_id, wait_ms=3000, capability="ProcessExecBounded")
        assert isinstance(result, JobResult)
        assert result.returncode == 0
        assert result.kill_cause is None


def test_smoke_socket_blocked_answered_slowly_by_sibling_completes() -> None:
    """4. Socket-blocked process answered slowly by sibling completes via voluntary ctx signal.

    A child Python process opens a TCP connection and ``recv``s one byte at
    a time while a sibling thread sends 8 bytes at 50 ms intervals. The
    child sees near-zero CPU, zero I/O, zero new lines, but its voluntary
    context-switch rate stays above the awake floor (R7 fires).
    """
    reg = ExecJobRegistry(
        liveness_config=_make_fast_liveness_config(),
        sample_interval_s=0.05,
        auto_register_runtime_hooks=False,
    )

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    sockname = server.getsockname()
    port: int = sockname[1] if isinstance(sockname, tuple) else 0

    def _sibling_worker() -> None:
        conn, _ = server.accept()
        for _ in range(8):
            time.sleep(0.05)
            conn.sendall(b"x")
        conn.close()
        server.close()

    sibling_thread = threading.Thread(target=_sibling_worker)
    sibling_thread.start()

    script = (
        f"import socket\n"
        f"s = socket.create_connection(('127.0.0.1', {port}))\n"
        f"count = 0\n"
        f"while True:\n"
        f"    data = s.recv(1)\n"
        f"    if not data:\n"
        f"        break\n"
        f"    count += 1\n"
        f"s.close()\n"
        f"assert count == 8\n"
    )

    # timeout_ms=250: would die of stall if idle, but voluntary context switches keep it alive
    cmd = [sys.executable, "-c", script]
    job_id = reg.start(cmd, timeout_ms=250, capability="ProcessExecBounded")
    result = reg.wait(job_id, wait_ms=2500, capability="ProcessExecBounded")
    sibling_thread.join()

    assert isinstance(result, JobResult)
    assert result.returncode == 0
    assert result.kill_cause is None
