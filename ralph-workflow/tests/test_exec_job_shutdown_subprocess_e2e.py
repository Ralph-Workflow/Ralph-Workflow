"""Real subprocess e2e tests for exec job lifecycle and shutdown hooks.

Tests real process trees under:
(a) SIGTERM delivery to a child server running an ExecJobRegistry, ensuring
    worker process tree termination and cause='session_end'.
(b) Owner disappearance, ensuring the child runtime's owner-loss watcher
    invokes the pre-exit hook and terminates the worker process tree.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(10),
]


def _wait_for_pid_dead(pid: int, timeout_s: float = 3.0) -> bool:
    """Poll until pid is no longer alive or timeout_s expires."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except ProcessLookupError:
            return True
        except OSError:
            # PID may be in zombie state or permission denied
            return True
    return False


def test_sigterm_shutdown_kills_job_tree() -> None:
    """Proof (a): SIGTERM to child running a job kills worker tree with cause session_end."""
    child_code = """
import os, sys, time
from ralph.mcp.server.runtime import install_shutdown_signal_handlers, register_shutdown_hook
from ralph.mcp.tools._exec_jobs import ExecJobRegistry, JobResult

install_shutdown_signal_handlers()
registry = ExecJobRegistry(auto_register_runtime_hooks=True)
job_id = registry.start([sys.executable, "-c", "import time; time.sleep(60)"], capability="ProcessExecBounded")
job = registry._running_jobs[job_id]
worker_pid = job.handle.pid

def _report_final_cause() -> None:
    res = registry.wait(job_id, wait_ms=0, capability="ProcessExecBounded")
    if isinstance(res, JobResult):
        print(f"CAUSE {res.kill_cause}", flush=True)

register_shutdown_hook(_report_final_cause)
print(f"READY {worker_pid}", flush=True)

while True:
    time.sleep(0.1)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", child_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None
    ready_line = child.stdout.readline()
    assert ready_line.startswith("READY "), f"Child failed to start job: {ready_line}"
    worker_pid = int(ready_line.split()[1])

    # Assert worker process is currently running
    os.kill(worker_pid, 0)

    # Send SIGTERM to child process
    child.terminate()

    # Read the CAUSE line reported by child shutdown hook
    cause_line = child.stdout.readline().strip()
    assert cause_line == "CAUSE session_end", f"Expected 'CAUSE session_end', got: {cause_line!r}"

    # Child should exit cleanly
    child.wait(timeout=5.0)

    # Worker process must have been terminated by child shutdown hook
    is_dead = _wait_for_pid_dead(worker_pid, timeout_s=3.0)
    assert is_dead, f"Worker process {worker_pid} was not terminated on SIGTERM"


def test_owner_loss_watcher_hook_kills_job_tree() -> None:
    """Proof (b): Owner-disappearance watcher invokes pre-exit hook killing worker tree."""
    owner_code = """
import os, sys, time, subprocess

child_code = '''
import os, sys, time
from ralph.mcp.server.runtime import exit_when_owner_exits
from ralph.mcp.tools._exec_jobs import ExecJobRegistry

# Poll with 50ms interval for fast test turnaround
exit_when_owner_exits(os.environ, sleep=lambda _: time.sleep(0.05))
registry = ExecJobRegistry(auto_register_runtime_hooks=True)
job_id = registry.start([sys.executable, "-c", "import time; time.sleep(60)"], capability="ProcessExecBounded")
job = registry._running_jobs[job_id]
print(f"CHILD_READY {job.handle.pid}", flush=True)

while True:
    time.sleep(0.1)
'''

env = dict(os.environ)
env["RALPH_MCP_OWNER_PID"] = str(os.getpid())
child = subprocess.Popen(
    [sys.executable, "-c", child_code],
    env=env,
    stdout=subprocess.PIPE,
    text=True,
)
assert child.stdout is not None
child_line = child.stdout.readline()
worker_pid = child_line.split()[1]
print(f"OWNER_READY {child.pid} {worker_pid}", flush=True)

while True:
    time.sleep(0.1)
"""
    owner = subprocess.Popen(
        [sys.executable, "-c", owner_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert owner.stdout is not None
    ready_line = owner.stdout.readline()
    assert ready_line.startswith("OWNER_READY "), f"Owner failed to start: {ready_line}"
    _, child_pid_str, worker_pid_str = ready_line.split()
    child_pid = int(child_pid_str)
    worker_pid = int(worker_pid_str)

    # Both child and worker must be running
    os.kill(child_pid, 0)
    os.kill(worker_pid, 0)

    # Kill the owner process to trigger owner disappearance
    owner.kill()
    owner.wait(timeout=5.0)

    # Child should detect reparenting and exit via exit_process(0)
    child_is_dead = _wait_for_pid_dead(child_pid, timeout_s=4.0)
    assert child_is_dead, f"Child server process {child_pid} failed to exit on owner loss"

    # Worker process must have been terminated by owner-loss hook
    worker_is_dead = _wait_for_pid_dead(worker_pid, timeout_s=3.0)
    assert worker_is_dead, f"Worker process {worker_pid} was not terminated on owner loss"
