"""Real-process end-to-end smoke tests for long-running exec jobs.

Validates the liveness assessor and ExecJobRegistry against real OS processes:
1. Real sleep process killed as stalled when silent and idle.
2. Silent Python CPU loop detected as suspicious after inactivity window.
3. Silent Python file-writer completes normally via I/O activity.
4. Socket-blocked process answered slowly by sibling completes via voluntary context switch signal.
"""

from __future__ import annotations

import socket
import sys
import tempfile
import threading
import time

import pytest

from ralph.mcp.tools._exec_jobs import ExecJobRegistry, JobResult, JobSnapshot
from ralph.process.manager._liveness_assessor import LivenessConfig

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
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


def test_smoke_real_sleep_killed_as_stalled() -> None:
    """1. Real sleep process is killed with cause 'stalled' due to zero activity."""
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


def test_smoke_silent_python_cpu_loop_suspicious() -> None:
    """2. Silent Python CPU loop remains running past inactivity window as suspicious."""
    reg = ExecJobRegistry(
        liveness_config=_make_fast_liveness_config(),
        sample_interval_s=0.05,
        auto_register_runtime_hooks=False,
    )

    script = "import time\nt = time.time() + 5\nwhile time.time() < t:\n    x = 1 + 1\n"
    cmd = [sys.executable, "-c", script]
    job_id = reg.start(cmd, timeout_ms=3000, capability="ProcessExecBounded")

    try:
        # Wait 0.35s so output aging is strictly past output_recent_max_s (0.2s)
        time.sleep(0.35)
        snapshot = reg.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
        assert isinstance(snapshot, JobSnapshot)
        assert snapshot.running is True
        assert snapshot.verdict is not None
        assert snapshot.verdict.suspicious is True
        assert snapshot.verdict.verdict_kind == "suspicious"
        assert snapshot.verdict.strongest_rule == "R4"
    finally:
        reg.cancel(job_id, capability="ProcessExecBounded")


def test_smoke_silent_python_file_writer_completes() -> None:
    """3. Silent Python file-writer completes cleanly via disk I/O activity."""
    reg = ExecJobRegistry(
        liveness_config=_make_fast_liveness_config(),
        sample_interval_s=0.05,
        auto_register_runtime_hooks=False,
    )

    with tempfile.NamedTemporaryFile() as tf:
        script = (
            f"import time\n"
            f"with open({tf.name!r}, 'wb') as f:\n"
            f"    for _ in range(5):\n"
            f"        f.write(b'x' * (512 * 1024))\n"
            f"        f.flush()\n"
            f"        time.sleep(0.04)\n"
        )
        cmd = [sys.executable, "-c", script]
        job_id = reg.start(cmd, timeout_ms=3000, capability="ProcessExecBounded")
        result = reg.wait(job_id, wait_ms=2500, capability="ProcessExecBounded")

        assert isinstance(result, JobResult)
        assert result.returncode == 0
        assert result.kill_cause is None


def test_smoke_socket_blocked_answered_slowly_by_sibling_completes() -> None:
    """4. Socket-blocked process answered slowly by sibling completes via voluntary ctx signal."""
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
