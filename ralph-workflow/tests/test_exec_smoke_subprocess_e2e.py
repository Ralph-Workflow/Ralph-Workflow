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
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(15),
]


def _make_fast_liveness_config() -> LivenessConfig:
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
    reg = ExecJobRegistry(
        liveness_config=_make_fast_liveness_config(),
        sample_interval_s=0.05,
        auto_register_runtime_hooks=False,
    )

    script = "import time\nend = time.time() + 4.0\nwhile time.time() < end:\n    x = 1 + 1\n"
    cmd = [sys.executable, "-c", script]
    job_id = reg.start(cmd, timeout_ms=1500, capability="ProcessExecBounded")

    try:
        saw_r4 = False
        saw_cpu_ratio = False
        min_deadline = time.monotonic() + 2.0
        max_deadline = time.monotonic() + 3.2
        while time.monotonic() < max_deadline:
            snapshot = reg.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
            assert isinstance(snapshot, JobSnapshot)
            assert snapshot.running is True, "CPU loop was killed despite R4 evidence"
            assert snapshot.verdict is not None
            cpu_ratio = snapshot.verdict.signals.get("cpu_ratio")
            if cpu_ratio is None:
                time.sleep(0.05)
                continue
            assert snapshot.verdict.verdict_kind in ("working", "suspicious"), (
                f"CPU loop classified as {snapshot.verdict.verdict_kind!r}; "
                "the liveness signal did not keep it alive"
            )
            if cpu_ratio >= 0.05:
                saw_cpu_ratio = True
            if snapshot.verdict.strongest_rule == "R4":
                saw_r4 = True
            if time.monotonic() >= min_deadline and saw_r4:
                break
            time.sleep(0.05)
        assert saw_r4, (
            "R4 never dominated any snapshot in the window; the CPU signal "
            "did not keep the process classified as silent compute"
        )
        assert saw_cpu_ratio, (
            "CPU signal unreadable (cpu_ratio < 0.05) in every snapshot; "
            "R4 could not have fired and the test cannot prove the "
            "responsible signal"
        )
    finally:
        reg.cancel(job_id, capability="ProcessExecBounded")


def test_smoke_silent_python_file_writer_runs_past_stall_window_then_completes() -> None:
    reg = ExecJobRegistry(
        liveness_config=_make_fast_liveness_config(),
        sample_interval_s=0.05,
        auto_register_runtime_hooks=False,
    )

    with tempfile.NamedTemporaryFile() as tf:
        script = (
            f"import time\n"
            f"with open({tf.name!r}, 'wb') as f:\n"
            f"    for i in range(80):\n"
            f"        f.write(b'x' * (256 * 1024))\n"
            f"        f.flush()\n"
            f"        time.sleep(0.04)\n"
        )
        cmd = [sys.executable, "-c", script]
        job_id = reg.start(cmd, timeout_ms=1500, capability="ProcessExecBounded")

        observe_until = time.monotonic() + 2.0
        while True:
            mid = reg.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
            assert isinstance(mid, JobSnapshot)
            assert mid.running is True, "file-writer was killed despite R2 evidence"
            if time.monotonic() >= observe_until:
                break
            time.sleep(0.05)

        result = reg.wait(job_id, wait_ms=5000, capability="ProcessExecBounded")
        assert isinstance(result, JobResult)
        assert result.returncode == 0
        assert result.kill_cause is None


def test_smoke_socket_blocked_answered_slowly_by_sibling_completes() -> None:
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
        for _ in range(40):
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
        f"assert count == 40\n"
    )

    cmd = [sys.executable, "-c", script]
    job_id = reg.start(cmd, timeout_ms=1500, capability="ProcessExecBounded")
    result = reg.wait(job_id, wait_ms=10000, capability="ProcessExecBounded")
    sibling_thread.join()

    assert isinstance(result, JobResult)
    assert result.returncode == 0
    assert result.kill_cause is None
