"""Real subprocess e2e tests for exec job lifecycle and shutdown hooks.

Tests real process trees under:
(a) SIGTERM delivery to a child server running an ExecJobRegistry, ensuring
    worker + descendant process tree termination and cause='session_end'.
(b) Owner disappearance, ensuring the child runtime's owner-loss watcher
    invokes the pre-exit hook and terminates the worker + descendant tree.
(c) Linux-only proof that a ProcessManager-spawned exec job dies when its
    parent is SIGKILLed via the kernel's ``PR_SET_PDEATHSIG=SIGKILL``
    mechanism installed by ``ralph.process.manager._install_pdeathsig``
    (``ralph/process/manager/__init__.py:90-108``). The MCP server itself
    dying has no Python hook to run; tree death on Linux depends on this
    mechanism. On non-Linux platforms the exec job tree is bounded only
    by registry caps and abandonment in a future server, so the test
    skips there.

Each test spawns a multi-process tree (worker → python → sleep child) so
the proof is structural, not just a single-PID check. The descendant
PID is written by the spawned worker into a sentinel file so the test
process can read it without relying on subprocess pipe timing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(20),
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
            return True
    return False


def _wait_for_sentinel(path: str, timeout_s: float = 5.0) -> int:
    """Read a PID from ``path`` once it is written, with a deadline."""
    sentinel = Path(path)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if sentinel.exists():
            try:
                with sentinel.open() as fh:
                    return int(fh.read().strip())
            except (OSError, ValueError):
                pass
        time.sleep(0.05)
    raise AssertionError(f"sentinel {path!r} never written within {timeout_s}s")


def _spawn_tree_script() -> str:
    """Return Python source for the registry-holding child.

    The spawned worker is a Python process that:
      1. Spawns ``sleep 60`` (a real grandchild of the registry).
      2. Writes the descendant PID to ``$DESCENDANT_FILE``.
      3. Waits forever so SIGTERM/SIGKILL must reap it.

    The registry itself writes the worker PID to ``$WORKER_FILE`` after
    the job is registered.
    """
    return (
        "import os, subprocess, sys, time\n"
        "worker_file = os.environ['WORKER_FILE']\n"
        "descendant_file = os.environ['DESCENDANT_FILE']\n"
        "from ralph.mcp.server.runtime import (\n"
        "    install_shutdown_signal_handlers,\n"
        "    register_shutdown_hook,\n"
        ")\n"
        "from ralph.mcp.tools._exec_jobs import ExecJobRegistry, JobResult\n"
        "\n"
        "install_shutdown_signal_handlers()\n"
        "registry = ExecJobRegistry(auto_register_runtime_hooks=True)\n"
        # Worker: Python process that spawns a real ``sleep`` grandchild\n"
        # and waits for it. The worker writes the descendant PID to a\n"
        # sentinel file so the test can read it without racing the\n"
        # registry's internal pipe.\n"
        "worker_script = (\n"
        "    'import os, subprocess, sys, time\\n'\n"
        '    \'p = subprocess.Popen(["sleep", "60"])\\n\'\n'
        '    \'open(os.environ["DESCENDANT_FILE"], "w").write(str(p.pid))\\n\'\n'
        "    'while True:\\n'\n"
        "    '    time.sleep(60)'\n"
        ")\n"
        "cmd = [sys.executable, '-u', '-c', worker_script]\n"
        "job_id = registry.start(cmd, capability='ProcessExecBounded')\n"
        "job = registry._running_jobs[job_id]\n"
        "worker_pid = job.handle.pid\n"
        "open(worker_file, 'w').write(str(worker_pid))\n"
        "\n"
        "def _report_final_cause() -> None:\n"
        "    res = registry.wait(job_id, wait_ms=0, capability='ProcessExecBounded')\n"
        "    if isinstance(res, JobResult):\n"
        "        print(f'CAUSE {res.kill_cause}', flush=True)\n"
        "\n"
        "register_shutdown_hook(_report_final_cause)\n"
        "while True:\n"
        "    time.sleep(0.1)\n"
    )


def test_sigterm_shutdown_kills_worker_and_descendant(
    tmp_path: object,
) -> None:
    """Proof (a): SIGTERM to child running a job kills the worker + descendant with cause session_end."""
    worker_file = f"{tmp_path}/worker.pid"
    descendant_file = f"{tmp_path}/descendant.pid"
    env = dict(os.environ)
    env["WORKER_FILE"] = worker_file
    env["DESCENDANT_FILE"] = descendant_file
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", _spawn_tree_script()],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert child.stdout is not None
    worker_pid = _wait_for_sentinel(worker_file, timeout_s=5.0)
    descendant_pid = _wait_for_sentinel(descendant_file, timeout_s=5.0)
    assert worker_pid > 0
    assert descendant_pid > 0
    # Both must be alive right now.
    os.kill(worker_pid, 0)
    os.kill(descendant_pid, 0)

    # Send SIGTERM to the registry-holding child.
    child.terminate()

    # Read the CAUSE line reported by the child shutdown hook.
    cause_line = child.stdout.readline().strip()
    assert cause_line == "CAUSE session_end", f"Expected 'CAUSE session_end', got: {cause_line!r}"

    # Child must exit cleanly.
    child.wait(timeout=5.0)

    # Both the worker AND its descendant must be terminated.
    worker_dead = _wait_for_pid_dead(worker_pid, timeout_s=3.0)
    descendant_dead = _wait_for_pid_dead(descendant_pid, timeout_s=3.0)
    assert worker_dead, f"Worker {worker_pid} not terminated on SIGTERM"
    assert descendant_dead, (
        f"Descendant {descendant_pid} not reaped on SIGTERM — tree cleanup incomplete"
    )


def test_owner_loss_watcher_kills_worker_and_descendant(
    tmp_path: object,
) -> None:
    """Proof (b): Owner-disappearance watcher invokes pre-exit hook killing worker + descendant.

    Distinct from SIGTERM (a) — the owner is killed abruptly so the
    registry's owner-loss watcher path fires (not the SIGTERM signal
    handler). Both the worker and its descendant sleep process must be
    reaped on owner death.
    """
    worker_file = f"{tmp_path}/worker.pid"
    descendant_file = f"{tmp_path}/descendant.pid"
    child_pid_file = f"{tmp_path}/child.pid"

    inner_child_code = (
        "import os, sys, time, subprocess\n"
        "from ralph.mcp.server.runtime import exit_when_owner_exits\n"
        "from ralph.mcp.tools._exec_jobs import ExecJobRegistry\n"
        "# Poll with 50ms interval for fast test turnaround.\n"
        "exit_when_owner_exits(os.environ, sleep=lambda _: time.sleep(0.05))\n"
        "registry = ExecJobRegistry(auto_register_runtime_hooks=True)\n"
        "worker_script = (\n"
        "    'import os, subprocess, sys, time\\n'\n"
        '    \'p = subprocess.Popen(["sleep", "60"])\\n\'\n'
        '    \'open(os.environ["DESCENDANT_FILE"], "w").write(str(p.pid))\\n\'\n'
        "    'while True:\\n'\n"
        "    '    time.sleep(60)'\n"
        ")\n"
        "cmd = [sys.executable, '-u', '-c', worker_script]\n"
        "job_id = registry.start(cmd, capability='ProcessExecBounded')\n"
        "job = registry._running_jobs[job_id]\n"
        "open(os.environ['WORKER_FILE'], 'w').write(str(job.handle.pid))\n"
        "open(os.environ['CHILD_FILE'], 'w').write(str(os.getpid()))\n"
        "while True:\n"
        "    time.sleep(0.1)\n"
    )

    owner_code = (
        "import os, sys, subprocess, time\n"
        "env = dict(os.environ)\n"
        "env['RALPH_MCP_OWNER_PID'] = str(os.getpid())\n"
        "child = subprocess.Popen(\n"
        "    [sys.executable, '-u', '-c', "
        f"{inner_child_code!r}],\n"
        "    env=env,\n"
        "    stdout=subprocess.DEVNULL,\n"
        "    stderr=subprocess.DEVNULL,\n"
        ")\n"
        "while True:\n"
        "    time.sleep(0.1)\n"
    )

    owner_env = dict(os.environ)
    owner_env["WORKER_FILE"] = worker_file
    owner_env["DESCENDANT_FILE"] = descendant_file
    owner_env["CHILD_FILE"] = child_pid_file
    owner = subprocess.Popen(
        [sys.executable, "-u", "-c", owner_code],
        env=owner_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    worker_pid = _wait_for_sentinel(worker_file, timeout_s=5.0)
    descendant_pid = _wait_for_sentinel(descendant_file, timeout_s=5.0)
    child_pid = _wait_for_sentinel(child_pid_file, timeout_s=5.0)
    assert worker_pid > 0 and descendant_pid > 0 and child_pid > 0
    # All three must be alive right now.
    os.kill(child_pid, 0)
    os.kill(worker_pid, 0)
    os.kill(descendant_pid, 0)

    # Kill the owner process to trigger owner disappearance.
    owner.kill()
    owner.wait(timeout=5.0)

    # The owner-loss watcher's pre-exit hook must terminate the worker
    # AND the descendant; the watcher then exits the child via exit_process(0).
    worker_dead = _wait_for_pid_dead(worker_pid, timeout_s=5.0)
    descendant_dead = _wait_for_pid_dead(descendant_pid, timeout_s=5.0)
    child_dead = _wait_for_pid_dead(child_pid, timeout_s=5.0)
    assert worker_dead, (
        f"Worker {worker_pid} was not terminated on owner loss — pre-exit "
        "hook did not kill the job tree"
    )
    assert descendant_dead, (
        f"Descendant {descendant_pid} was not reaped on owner loss — tree cleanup incomplete"
    )
    assert child_dead, f"Child server {child_pid} failed to exit on owner loss"


# Linux-only: PR_SET_PDEATHSIG is a Linux-only mechanism. macOS lacks an
# equivalent; on other Unix platforms the exec job tree is bounded only by
# registry caps and abandonment in a future server (a future CPR-killer
# helper). Skip the proof rather than silently passing on a platform where
# the mechanism cannot fire.
_LINUX_ONLY: pytest.MarkDecorator = pytest.mark.skipif(
    sys.platform != "linux",
    reason=(
        "PR_SET_PDEATHSIG is Linux-only; on other platforms the exec job "
        "tree is bounded only by registry caps and abandonment in a "
        "future server"
    ),
)


@_LINUX_ONLY
def test_parent_sigkill_kills_exec_job_via_pr_set_pdeathsig(
    tmp_path: object,
) -> None:
    """Proof (c): a ProcessManager-spawned exec job dies when its PARENT is SIGKILLed.

    Distinct from SIGTERM (a) and owner-loss (b):

    * (a) goes through ``install_shutdown_signal_handlers`` → ``_run_shutdown_hooks``
      → ``registry.shutdown_all``. The Python hook on the registry sends the
      kill itself.
    * (b) goes through ``exit_when_owner_exits`` /
      ``_run_owner_loss_hooks`` → ``registry.shutdown_all``. Again a Python
      hook on the registry sends the kill.
    * (c) bypasses every Python hook. No Python code in the registry or
      server is allowed to run after the parent receives SIGKILL — atexit
      and signal handlers are skipped. Tree death relies on the Linux
      kernel sending SIGKILL to children whose ``PR_SET_PDEATHSIG`` was
      armed by ``ralph.process.manager._install_pdeathsig`` at the
      ProcessManager preexec (``ralph/process/manager/__init__.py:90-108``).
      When that preexec runs (Linux + main thread), the worker's effective
      parent is the holder, so the kernel signals the worker when the
      holder dies.

    The holder is a real Python intermediate-parent process: it constructs
    an ``ExecJobRegistry`` and calls ``registry.start(...)`` exactly the
    way the MCP server does. The exec-job worker therefore has the holder
    as its real parent (recorded in the kernel's task struct, not just a
    Python notion). When the test SIGKILLs the holder, the worker must
    die — that is the proof this test exists to capture.

    The exec-job command itself is a ``sleep`` long-runner, so the
    worker IS the long-running exec, not a wrapper around it. We do NOT
    add a wrapper/grandchild layer: PR_SET_PDEATHSIG is a *per-process*
    setting that survives fork but is not reliably propagated one more
    layer down through Python's ``subprocess.Popen`` (it can be reset by
    ``exec`` to an unrelated program, depending on the kernel and the
    way Python's process machinery treats prctl across the post-fork
    exec boundary). Asserting on the worker alone matches the
    real-world threat model: a SIGKILL'd MCP server's exec jobs must die
    without any Python hook firing.

    Shrink all windows through config so the test finishes in ~1-2
    seconds. The total wall budget for the file stays well under the
    60-second combined test cap.
    """
    holder_pid_file = f"{tmp_path}/holder.pid"
    worker_file = f"{tmp_path}/worker.pid"

    # Holder: a real Python intermediate-parent process that constructs
    # an ExecJobRegistry and starts a long-running exec job, exactly the
    # way the MCP server does. The exec-job command is a ``sleep 60``
    # so the exec job itself is the long-running process whose parent
    # is the holder; that parent is what PR_SET_PDEATHSIG was armed
    # against by the ProcessManager preexec.
    holder_code = """
import os, sys, time
from ralph.mcp.tools._exec_jobs import ExecJobRegistry

cmd = ["sleep", "60"]
registry = ExecJobRegistry(auto_register_runtime_hooks=True)
job_id = registry.start(cmd, capability='ProcessExecBounded')
job = registry._running_jobs[job_id]
open(os.environ['HOLDER_FILE'], 'w').write(str(os.getpid()))
open(os.environ['WORKER_FILE'], 'w').write(str(job.handle.pid))
while True:
    time.sleep(0.1)
"""

    holder_env = dict(os.environ)
    holder_env["HOLDER_FILE"] = holder_pid_file
    holder_env["WORKER_FILE"] = worker_file
    holder = subprocess.Popen(
        [sys.executable, "-u", "-c", holder_code],
        env=holder_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    holder_pid = _wait_for_sentinel(holder_pid_file, timeout_s=5.0)
    worker_pid = _wait_for_sentinel(worker_file, timeout_s=5.0)
    assert holder_pid > 0
    assert worker_pid > 0

    # Both must be alive right now.
    os.kill(holder_pid, 0)
    os.kill(worker_pid, 0)

    holder.kill()  # SIGKILL — atexit/signal handlers DO NOT fire on SIGKILL.
    holder.wait(timeout=2.0)

    # The registered PR_SET_PDEATHSIG=SIGKILL on the worker matches the
    # holder's death. The worker must die well inside a one-second window.
    worker_dead = _wait_for_pid_dead(worker_pid, timeout_s=1.5)
    assert worker_dead, (
        f"Worker {worker_pid} was not reaped on parent SIGKILL — "
        "PR_SET_PDEATHSIG installed by ralph.process.manager did not fire"
    )
