"""Real subprocess e2e tests for exec job lifecycle and shutdown hooks.

Tests real process trees under:
(a) SIGTERM delivery to a child server running an ExecJobRegistry, ensuring
    worker + descendant process tree termination and cause='session_end'.
(b) Owner disappearance, ensuring the child runtime's owner-loss watcher
    invokes the pre-exit hook and terminates the worker + descendant tree.
(c) Linux-only proof that a ProcessManager-spawned exec job dies when its
    parent is SIGKILLed via the kernel's ``PR_SET_PDEATHSIG=SIGKILL``
    mechanism installed by ``ralph.process.manager._install_pdeathsig``
    (``ralph/process/manager/__init__.py:94-112``). The MCP server itself
    dying has no Python hook to run; tree death on Linux depends on this
    mechanism. There is no cross-platform parent-death equivalent, so once
    the server is SIGKILLed on a non-Linux platform the orphaned tree has
    no in-process lifetime bound at all and the test skips there.
(d) The four liveness kill causes -- ``stalled``, ``no_progress``,
    ``hard_cap`` and ``abandoned`` -- each proven to reap a real worker ->
    descendant tree, not just the root. Cause *termination* for those four is
    already covered on fake processes by ``tests/test_exec_jobs.py``; that file
    explicitly scopes its cleanup evidence to root-only (see its
    ``CLEANUP-EVIDENCE SCOPE`` note), which is exactly the gap these proofs
    close.

Each test spawns a multi-process tree (worker -> python -> sleep child) so
the proof is structural, not just a single-PID check. The descendant
PID is written by the spawned worker into a sentinel file so the test
process can read it without relying on subprocess pipe timing.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import pytest

from ralph.executor import ProcessExecutionError, ProcessResult, run_process
from ralph.executor.process import ProcessRunOptions
from ralph.process.manager import ManagedProcess, SpawnOptions, get_process_manager

if TYPE_CHECKING:
    from collections.abc import Callable

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


# Linux-only: PR_SET_PDEATHSIG is a Linux-only mechanism — there is no
# per-platform equivalent on macOS/Windows. Once the MCP server is
# SIGKILLed, there is NO in-process lifetime bound for an orphaned
# exec-job tree: the ExecJobRegistry, liveness sampler threads, the
# abandonment timer, and signal/atexit hooks are all part of the
# now-dead server process and stop running with it. The only real
# mitigations would be an external supervisor (out of scope here) or a
# cross-platform parent-death mechanism that does not currently exist
# on macOS/Windows. Skip the proof rather than silently passing on a
# platform where the mechanism cannot fire.
_LINUX_ONLY: pytest.MarkDecorator = pytest.mark.skipif(
    sys.platform != "linux",
    reason=(
        "PR_SET_PDEATHSIG is Linux-only and no cross-platform "
        "parent-death equivalent is available; once the MCP server is "
        "SIGKILLed on a non-Linux platform there is no in-process "
        "lifetime bound for an orphaned exec-job tree"
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
      ProcessManager preexec (``ralph/process/manager/__init__.py:94-112``).
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


@_LINUX_ONLY
def test_worker_thread_sigkill_kills_exec_job_tree_and_its_descendant(
    tmp_path: object,
) -> None:
    """Proof (d): non-main worker thread SIGKILL reaps the worker AND its descendant.

    Distinct from proof (c) above. Proof (c) starts the registry in a
    holder MAIN thread, where the legacy ``preexec_fn``
    ``_install_pdeathsig`` path fires; it also asserts only the direct
    child. This proof closes both gaps simultaneously:

    * Worker thread shape. Production HTTP dispatch runs the exec tool
      callback inside a ``ThreadPoolExecutor`` worker thread
      (``ralph/mcp/server/_saturated_dispatch.py:155-173`` ->
      ``ralph/mcp/tools/exec.py:807-814`` ->
      ``ralph/mcp/tools/_exec_jobs.py:665-675`` ->
      ``ProcessManager.spawn``). Python's stdlib refuses ``preexec_fn``
      from a non-main thread, so the legacy main-thread-only
      ``_install_pdeathsig`` is skipped and the worker survives SIGKILL
      orphaned to init. This test mirrors that path 1:1 -- the holder
      process creates an ``ExecJobRegistry`` on its main thread and then
      invokes ``registry.start`` from a
      ``concurrent.futures.ThreadPoolExecutor`` worker, the same executor
      the dispatcher uses.

    * Descendant coverage. The exec-job worker is a Python script that
      forks an actual ``sleep 600`` and writes the descendant's PID to a
      sentinel file. ``PR_SET_PDEATHSIG`` is per-process; it does not
      cascade to grandchildren. The supervisor mechanism
      (``ralph/process/manager/_supervisor.py``) closes that gap by
      spawning supervisor + worker in the same fresh PG (the supervisor
      is started with ``start_new_session=True`` and the worker inherits
      that PG at fork), so the supervisor's SIGTERM handler
      ``killpg``s the entire PG on holder death, taking the worker AND
      every descendant in one sweep.

    SIGKILL on the holder is required -- SIGTERM would let the registry's
    shutdown hooks (``install_shutdown_signal_handlers`` -> ``shutdown_all``)
    reap the tree through the Python path documented in proof (a), not
    exercise the supervisor. Both ``worker_pid`` (the supervisor's
    execvp'd worker) and ``descendant_pid`` (the backgrounded ``sleep``)
    must be gone within a bounded deadline; the deadline is loose enough
    to absorb a Python signal-handler trampoline but tight enough that a
    regression that loses the cascade cannot accidentally finish before
    it's observed.

    A note on which PIDs the test observes: with the supervisor in the
    production path, ``job.handle.pid`` is the supervisor's PID, not the
    worker's. The worker's PID is the supervisor's direct child (the
    forked process that became the user-visible ``sh``/``python`` exec),
    so the holder polls ``psutil.Process(supervisor_pid).children()``
    until a live child appears and writes that PID to a sentinel. The
    descendant is the worker's child, recorded by the worker's own
    ``Popen`` call into ``$DESCENDANT_FILE``.
    """
    holder_pid_file = f"{tmp_path}/holder.pid"
    supervisor_pid_file = f"{tmp_path}/supervisor.pid"
    worker_pid_file = f"{tmp_path}/worker.pid"
    descendant_pid_file = f"{tmp_path}/descendant.pid"

    # Holder: a real Python intermediate-parent process that constructs
    # an ExecJobRegistry on the main thread and starts the long-running
    # exec job from a ThreadPoolExecutor worker -- the exact shape
    # production HTTP dispatch uses. The exec-job worker is a small
    # Python wrapper that forks a ``sleep 600`` child and writes the
    # child's PID to ``$DESCENDANT_FILE`` before sleeping itself. The
    # holder records its own PID, the supervisor's PID (via
    # ``job.handle.pid``), and the descendant's PID to sentinels so the
    # test process can read them deterministically.
    holder_code = (
        "import concurrent.futures\n"
        "import os\n"
        "import sys\n"
        "import threading\n"
        "import time\n"
        "\n"
        "import psutil\n"
        "\n"
        "from ralph.mcp.tools._exec_jobs import ExecJobRegistry\n"
        "\n"
        "_HOLDER_FILE = os.environ['HOLDER_FILE']\n"
        "_SUPERVISOR_FILE = os.environ['SUPERVISOR_FILE']\n"
        "_WORKER_FILE = os.environ['WORKER_FILE']\n"
        "_DESCENDANT_FILE = os.environ['DESCENDANT_FILE']\n"
        "\n"
        "_REGISTRY = ExecJobRegistry(auto_register_runtime_hooks=True)\n"
        "\n"
        "# Worker command: a Python wrapper that forks ``sleep 600`` and\n"
        "# writes the descendant's PID to ``$DESCENDANT_FILE`` so the test\n"
        "# can read the descendant without racing the supervisor's pipe.\n"
        "# The wrapper itself sleeps forever so SIGKILL on the holder must\n"
        "# cascade through the supervisor mechanism for the test to see\n"
        "# the wrapper die.\n"
        "_WORKER_CMD = [\n"
        "    sys.executable,\n"
        "    '-u',\n"
        "    '-c',\n"
        "    (\n"
        "        'import os, subprocess, time\\n'\n"
        '        \'p = subprocess.Popen(["sleep", "600"])\\n\'\n'
        '        \'open(os.environ["DESCENDANT_FILE"], "w").write(str(p.pid))\\n\'\n'
        "        'while True:\\n'\n"
        "        '    time.sleep(60)\\n'\n"
        "    ),\n"
        "]\n"
        "\n"
        "def _start_job() -> None:\n"
        "    # This runs on a ThreadPoolExecutor worker thread -- same shape\n"
        "    # as production HTTP dispatch\n"
        "    # (``ralph/mcp/server/_saturated_dispatch.py:155-173``). The legacy\n"
        "    # ``preexec_fn`` PDEATHSIG is skipped; the supervisor mechanism must\n"
        "    # take over.\n"
        "    assert threading.current_thread() is not threading.main_thread(), (\n"
        "        'test invariant broken: registry.start must run off the main thread so the supervisor mechanism is exercised',\n"
        "    )\n"
        "    job_id = _REGISTRY.start(_WORKER_CMD, capability='ProcessExecBounded')\n"
        "    job = _REGISTRY._running_jobs[job_id]\n"
        "    open(_SUPERVISOR_FILE, 'w').write(str(job.handle.pid))\n"
        "\n"
        "# Main-thread bookkeeping: announce the holder, fire the worker,\n"
        "# then walk psutil to discover the supervisor's actual child\n"
        "# (the exec'd worker). Recording the supervisor's PID separately\n"
        "# means the test can name both layers in its assertions.\n"
        "open(_HOLDER_FILE, 'w').write(str(os.getpid()))\n"
        "_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1)\n"
        "_FUTURE = _EXECUTOR.submit(_start_job)\n"
        "\n"
        "_DEADLINE = time.monotonic() + 5.0\n"
        "_SUPERVISOR_PID = None\n"
        "_WORKER_PID = None\n"
        "while time.monotonic() < _DEADLINE and (_SUPERVISOR_PID is None or _WORKER_PID is None):\n"
        "    try:\n"
        "        _SUPERVISOR_PID = int(open(_SUPERVISOR_FILE).read().strip())\n"
        "    except (OSError, ValueError):\n"
        "        pass\n"
        "    if _SUPERVISOR_PID is not None:\n"
        "        try:\n"
        "            _CHILDREN = psutil.Process(_SUPERVISOR_PID).children()\n"
        "            if _CHILDREN:\n"
        "                _WORKER_PID = _CHILDREN[0].pid\n"
        "        except (psutil.NoSuchProcess, psutil.AccessDenied):\n"
        "            pass\n"
        "    _FUTURE.result(timeout=0.5)\n"
        "    time.sleep(0.05)\n"
        "\n"
        "if _SUPERVISOR_PID is None:\n"
        "    raise SystemExit(f'supervisor sentinel never appeared at {_SUPERVISOR_FILE!r}')\n"
        "if _WORKER_PID is None:\n"
        "    raise SystemExit('could not enumerate supervisor children within 5s')\n"
        "open(_WORKER_FILE, 'w').write(str(_WORKER_PID))\n"
        "\n"
        "while True:\n"
        "    time.sleep(0.1)\n"
    )

    holder_env = dict(os.environ)
    holder_env["HOLDER_FILE"] = holder_pid_file
    holder_env["SUPERVISOR_FILE"] = supervisor_pid_file
    holder_env["WORKER_FILE"] = worker_pid_file
    holder_env["DESCENDANT_FILE"] = descendant_pid_file
    holder = subprocess.Popen(
        [sys.executable, "-u", "-c", holder_code],
        env=holder_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    # Pre-bind so the ``finally`` cleanup can run even if the body raises
    # before the sentinel reads -- the test must not leave child processes
    # behind when an assertion fails on the first ``_wait_for_sentinel``.
    holder_pid = 0
    supervisor_pid = 0
    worker_pid = 0
    descendant_pid = 0
    try:
        holder_pid = _wait_for_sentinel(holder_pid_file, timeout_s=5.0)
        supervisor_pid = _wait_for_sentinel(supervisor_pid_file, timeout_s=5.0)
        worker_pid = _wait_for_sentinel(worker_pid_file, timeout_s=10.0)
        descendant_pid = _wait_for_sentinel(descendant_pid_file, timeout_s=10.0)
        assert holder_pid > 0
        assert supervisor_pid > 0
        assert worker_pid > 0
        assert descendant_pid > 0
        # The supervisor's child must be the worker PID we recorded via
        # psutil, not the supervisor itself -- otherwise the production
        # boundary is broken.
        assert supervisor_pid != worker_pid, (
            f"supervisor_pid ({supervisor_pid}) must not collide with worker_pid ({worker_pid})"
        )

        # Sanity: every participant must be a live process before we
        # SIGKILL the holder. ``os.kill(pid, 0)`` raises if the pid is
        # gone, so the bare call doubles as an existence assertion.
        os.kill(holder_pid, 0)
        os.kill(supervisor_pid, 0)
        os.kill(worker_pid, 0)
        os.kill(descendant_pid, 0)

        holder.kill()  # SIGKILL -- atexit / signal handlers DO NOT fire.
        holder.wait(timeout=2.0)

        # The supervisor's SIGTERM-handler ``killpg``s the supervisor's
        # PG (== worker's PG == every descendant's PG) the instant the
        # kernel delivers SIGTERM via the supervisor's PR_SET_PDEATHSIG.
        # The worker's own PR_SET_PDEATHSIG cascade catches any race
        # where the supervisor itself is killed via SIGKILL before the
        # killpg call can run. Both paths converge here; the deadline is
        # wide enough to absorb the Python signal-handler trampoline on
        # a contended CI box but tight enough that a regression that
        # loses the cascade cannot accidentally pass.
        supervisor_dead = _wait_for_pid_dead(supervisor_pid, timeout_s=3.0)
        worker_dead = _wait_for_pid_dead(worker_pid, timeout_s=3.0)
        descendant_dead = _wait_for_pid_dead(descendant_pid, timeout_s=3.0)
        assert supervisor_dead, (
            f"Supervisor {supervisor_pid} was not reaped on holder SIGKILL -- "
            "PR_SET_PDEATHSIG on the supervisor did not fire"
        )
        assert worker_dead, (
            f"Worker {worker_pid} was not reaped on holder SIGKILL -- "
            "the supervisor mechanism did not cascade to a worker "
            "spawned from a non-main thread"
        )
        assert descendant_dead, (
            f"Descendant {descendant_pid} was not reaped on holder SIGKILL "
            "-- tree cleanup incomplete; PR_SET_PDEATHSIG alone does not "
            "cascade to grandchildren, the killpg sweep is required"
        )
    finally:
        # Belt and suspenders: tear the holder down if anything raised
        # before the SIGKILL fired so the host is not left holding a
        # long-running Python child during the test cleanup phase.
        # Each ``os.kill`` is ``suppress``-ed because ProcessLookupError
        # IS the success path here -- the SIGKILL sweep was the goal.
        if holder.poll() is None:
            with __import__("contextlib").suppress(Exception):
                holder.kill()
                holder.wait(timeout=2.0)
        for pid in (worker_pid, descendant_pid, supervisor_pid):
            if not pid or pid <= 0:
                continue
            with __import__("contextlib").suppress(ProcessLookupError, OSError):
                os.kill(pid, 9)


def _on_worker_thread(action: Callable[[], object]) -> object:
    """Run ``action`` on a non-main thread, where the supervisor path applies."""
    outcome: dict[str, object] = {}

    def run() -> None:
        try:
            outcome["value"] = action()
        except Exception as error:
            outcome["error"] = error

    worker = threading.Thread(target=run, name="supervised-spawn")
    worker.start()
    worker.join(timeout=10.0)
    error = outcome.get("error")
    if isinstance(error, Exception):
        raise error
    return outcome["value"]


@_LINUX_ONLY
def test_supervised_spawn_does_not_import_ralph_from_the_child_cwd(tmp_path: Path) -> None:
    """An exec job whose cwd holds a ``ralph`` package must still run normally."""
    shadow = tmp_path / "ralph"
    shadow.mkdir()
    (shadow / "__init__.py").write_text('raise SystemExit("shadowed")\n', encoding="utf-8")

    result = _on_worker_thread(
        lambda: run_process("/bin/echo", ("hi",), options=ProcessRunOptions(cwd=tmp_path))
    )

    assert isinstance(result, ProcessResult)
    assert (result.returncode, result.stdout, result.stderr) == (0, "hi\n", "")


@_LINUX_ONLY
def test_supervised_spawn_of_a_missing_executable_raises() -> None:
    with pytest.raises(ProcessExecutionError, match="No such file"):
        _on_worker_thread(lambda: run_process("/nonexistent/ralph-missing-binary"))


def _reported_parent_pid(options: SpawnOptions) -> int:
    command = [sys.executable, "-c", "import os; print(os.getppid())"]
    handle = _on_worker_thread(lambda: get_process_manager().spawn(command, options))
    assert isinstance(handle, ManagedProcess)
    stdout, _ = handle.communicate(timeout=10.0)
    assert isinstance(stdout, bytes)
    return int(stdout)


@_LINUX_ONLY
def test_child_requiring_a_direct_parent_sees_its_spawner() -> None:
    """The MCP server's owner watch compares ``getppid()`` with its owner."""
    options = SpawnOptions(stdout=subprocess.PIPE, require_direct_parent=True)

    assert _reported_parent_pid(options) == os.getpid()


@_LINUX_ONLY
def test_supervised_child_is_not_a_direct_child_of_its_spawner() -> None:
    assert _reported_parent_pid(SpawnOptions(stdout=subprocess.PIPE)) != os.getpid()


_GRACEFUL_WORKER = """
import pathlib, signal, sys, time
def stop(*_):
    time.sleep(0.3)
    pathlib.Path(sys.argv[1]).write_text("graceful")
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
pathlib.Path(sys.argv[2]).write_text("ready")
time.sleep(30)
"""


@_LINUX_ONLY
def test_terminating_a_supervised_job_keeps_the_worker_grace_period(tmp_path: Path) -> None:
    """A deliberate terminate must not be upgraded to an immediate tree SIGKILL."""
    graceful = tmp_path / "graceful"
    ready = tmp_path / "ready"
    command = [sys.executable, "-c", _GRACEFUL_WORKER, str(graceful), str(ready)]
    options = SpawnOptions(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    handle = _on_worker_thread(lambda: get_process_manager().spawn(command, options))
    assert isinstance(handle, ManagedProcess)
    _wait_for_sentinel_text(ready)

    handle.terminate(grace_period_s=5.0)

    assert graceful.read_text(encoding="utf-8") == "graceful"


def _wait_for_sentinel_text(path: Path, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"{path} never written within {timeout_s}s")
        time.sleep(0.02)


# ---------------------------------------------------------------------------
# Proof (e) — liveness-cause tree reaping.
#
# Acceptance criterion 5 requires that "every kill cause ... is covered by a
# test, AND the process tree is cleaned up in each case". ``tests/test_exec_jobs.py``
# covers the four liveness causes' *termination* on fake process doubles and
# self-declares its cleanup evidence as root-only (its ``CLEANUP-EVIDENCE SCOPE``
# note). These proofs close that second clause on real trees: each drives the
# production ``_exec_jobs.ExecJobRegistry.step_job`` kill branch
# (``_exec_jobs.py:498-549``) until it reports its own kill cause, and then
# asserts that BOTH the exec worker AND the worker's own child are gone.
#
# The production kill path under test is identical for all four causes:
# ``_kill_job`` -> ``ManagedProcess.terminate(grace_period_s=0.0)``
# (``_exec_jobs.py:442-460``) -> ``ProcessManager._escalate_termination_sync``
# -> ``_find_procs_to_terminate``, which returns
# ``[root, *root.children(recursive=True)]``
# (``ralph/process/manager/_process_manager.py:1716-1727``). So the descendant
# reaping is real, not incidental; these tests exist to keep it that way.
#
# Only the CLOCK is injected. The holder drives the real registry with the
# real ``step_job`` and the real ``ProcessManager``; ``clock=`` plus
# ``background_sampler=False`` let the multi-minute production windows
# (120 s abandonment, 900 s no-progress, 30 s liveness evidence window) be
# crossed inside the 60 s combined test budget without touching a single
# timeout constant. Every process, signal, verdict and kill is production.
# ---------------------------------------------------------------------------

#: The exec-job command under test. It forks a real ``sleep 600`` grandchild,
#: records that grandchild's PID, then goes silent. ``WORKER_SPIN=1`` makes it
#: a real CPU burner instead, which is what drives ``CpuBusy`` -> ``R4``
#: (silent compute, suspicious) and therefore the no-progress clock.
#: It prints NOTHING, so ``last_output_at`` stays ``None`` and the registry
#: samples the never-output window -- the shape criterion C3 is literally met
#: on (``tests/test_exec_liveness_scenarios.py`` scenario 14).
_LIVENESS_TREE_WORKER = """
import os, subprocess, time

_descendant = subprocess.Popen(["sleep", "600"])
with open(os.environ["DESCENDANT_FILE"], "w") as _fh:
    _fh.write(str(_descendant.pid))
if os.environ.get("WORKER_SPIN") == "1":
    while True:  # real CPU burner: CpuBusy saturates -> R4 (silent compute)
        pass
while True:
    time.sleep(60)
"""


def _liveness_holder_script() -> str:
    """Return the holder source that drives one real liveness kill to completion.

    The holder owns a real :class:`ExecJobRegistry` (and, through it, the
    process-local ``ProcessManager``) and starts one real exec job whose
    command is :data:`_LIVENESS_TREE_WORKER`. It then:

    1. publishes the worker's PID and blocks until the worker has published
       its descendant PID and the test has armed ``$GO_FILE`` — so the test
       can prove BOTH PIDs are alive *before* any kill is requested;
    2. runs ``AGE_STEPS`` aging steps with ``dt=0``. The fake clock jumps past
       ``EXEC_LIVENESS_EVIDENCE_WINDOW_S`` so the worker's own fork ages out
       of the sampler's windowed child-event history. Without this the
       ``TreeActive`` membership keeps ``R3`` (``z = 0.9``) firing, the stall
       score never drops below theta, and the stall branch is unreachable —
       which is the designed C2 behaviour ("working commands must not time
       out easily"), not a defect, but it would mask the branch under test;
    3. runs ``DRIVE_STEPS`` steps that advance the fake clock and hand the
       real integration ``dt`` to ``step_job``, calling ``wait()`` between
       steps when ``REFRESH_WAIT=1`` so the abandonment branch cannot fire
       ahead of the branch under test;
    4. prints the kill cause the production code recorded, plus the final
       verdict, and stays alive so the test can observe the tree.

    Every knob is an env var so one holder serves all four causes without
    branching in Python.
    """
    return (
        "import os, sys, time\n"
        "from ralph.mcp.tools._exec_jobs import ExecJobRegistry\n"
        "_clock = [0.0]\n"
        "_registry = ExecJobRegistry(\n"
        "    clock=lambda: _clock[0],\n"
        "    background_sampler=False,\n"
        "    auto_register_runtime_hooks=False,\n"
        ")\n"
        "_cmd = [sys.executable, '-u', '-c', os.environ['WORKER_CODE']]\n"
        "_job_id = _registry.start(\n"
        "    _cmd,\n"
        "    capability='ProcessExecBounded',\n"
        "    timeout_ms=int(os.environ['TIMEOUT_MS']),\n"
        "    max_runtime_ms=int(os.environ['MAX_RUNTIME_MS']),\n"
        ")\n"
        "_job = _registry._running_jobs[_job_id]\n"
        "with open(os.environ['WORKER_FILE'], 'w') as _fh:\n"
        "    _fh.write(str(_job.handle.pid))\n"
        "_deadline = time.monotonic() + 15.0\n"
        "while not os.path.exists(os.environ['DESCENDANT_FILE']):\n"
        "    if time.monotonic() > _deadline:\n"
        "        raise SystemExit('descendant sentinel never appeared')\n"
        "    time.sleep(0.02)\n"
        "while not os.path.exists(os.environ['GO_FILE']):\n"
        "    if time.monotonic() > _deadline:\n"
        "        raise SystemExit('go sentinel never appeared')\n"
        "    time.sleep(0.02)\n"
        "for _ in range(int(os.environ['AGE_STEPS'])):\n"
        "    _clock[0] += float(os.environ['AGE_CLOCK_STEP_S'])\n"
        "    _registry.step_job(_job_id, dt=0.0)\n"
        "_refresh = os.environ['REFRESH_WAIT'] == '1'\n"
        "_drive_dt = float(os.environ['DRIVE_DT_S'])\n"
        "for _ in range(int(os.environ['DRIVE_STEPS'])):\n"
        "    time.sleep(float(os.environ['DRIVE_REAL_SLEEP_S']))\n"
        "    _clock[0] += float(os.environ['DRIVE_CLOCK_STEP_S'])\n"
        "    if _refresh:\n"
        "        _registry.wait(_job_id, wait_ms=0, capability='ProcessExecBounded')\n"
        "    _registry.step_job(_job_id, dt=_drive_dt)\n"
        "    if _job_id not in _registry._running_jobs:\n"
        "        break\n"
        "_res = _registry.wait(_job_id, wait_ms=0, capability='ProcessExecBounded')\n"
        "_verdict = getattr(_res, 'verdict', None)\n"
        "print('CAUSE ' + str(getattr(_res, 'kill_cause', 'no-result')), flush=True)\n"
        "print(\n"
        "    'VERDICT '\n"
        "    + str(getattr(_verdict, 'verdict_kind', ''))\n"
        "    + ' '\n"
        "    + str(getattr(_verdict, 'strongest_rule', ''))\n"
        "    + ' '\n"
        "    + str(getattr(_verdict, 'score', '')),\n"
        "    flush=True,\n"
        ")\n"
        "while True:\n"
        "    time.sleep(0.1)\n"
    )


class _LivenessKillOutcome(NamedTuple):
    """What one liveness-cause run produced, as observed by the test process."""

    cause: str
    verdict: str
    worker_pid: int
    descendant_pid: int
    worker_dead: bool
    descendant_dead: bool


def _run_liveness_kill_case(
    tmp_path: Path,
    *,
    age_steps: int,
    age_clock_step_s: float,
    drive_steps: int,
    drive_clock_step_s: float,
    drive_dt_s: float,
    drive_real_sleep_s: float,
    refresh_wait: bool,
    timeout_ms: int,
    max_runtime_ms: int,
    spin: bool,
) -> _LivenessKillOutcome:
    """Drive one real liveness kill and report cause + per-PID tree liveness.

    Asserts only what every caller shares -- the tree really existed, i.e.
    both PIDs were live before the kill was requested. The recorded kill cause
    and the two death booleans are returned so each caller states its own
    expectations in its own test body.
    """
    worker_file = f"{tmp_path}/liveness_worker.pid"
    descendant_file = f"{tmp_path}/liveness_descendant.pid"
    go_file = f"{tmp_path}/liveness_go"

    env = dict(os.environ)
    env["WORKER_FILE"] = worker_file
    env["DESCENDANT_FILE"] = descendant_file
    env["GO_FILE"] = go_file
    env["WORKER_CODE"] = _LIVENESS_TREE_WORKER
    env["WORKER_SPIN"] = "1" if spin else "0"
    env["TIMEOUT_MS"] = str(timeout_ms)
    env["MAX_RUNTIME_MS"] = str(max_runtime_ms)
    env["AGE_STEPS"] = str(age_steps)
    env["AGE_CLOCK_STEP_S"] = repr(age_clock_step_s)
    env["DRIVE_STEPS"] = str(drive_steps)
    env["DRIVE_CLOCK_STEP_S"] = repr(drive_clock_step_s)
    env["DRIVE_DT_S"] = repr(drive_dt_s)
    env["DRIVE_REAL_SLEEP_S"] = repr(drive_real_sleep_s)
    env["REFRESH_WAIT"] = "1" if refresh_wait else "0"

    holder = subprocess.Popen(
        [sys.executable, "-u", "-c", _liveness_holder_script()],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    worker_pid = 0
    descendant_pid = 0
    try:
        worker_pid = _wait_for_sentinel(worker_file, timeout_s=10.0)
        descendant_pid = _wait_for_sentinel(descendant_file, timeout_s=10.0)
        assert worker_pid > 0
        assert descendant_pid > 0
        # Both PIDs must be live before the kill is even requested, otherwise
        # "the descendant is gone afterwards" would prove nothing.
        os.kill(worker_pid, 0)
        os.kill(descendant_pid, 0)

        Path(go_file).touch()

        cause_line = holder.stdout.readline().strip()
        verdict_line = holder.stdout.readline().strip()
        cause = cause_line.removeprefix("CAUSE ")
        verdict = verdict_line.removeprefix("VERDICT ")

        worker_dead = _wait_for_pid_dead(worker_pid, timeout_s=5.0)
        descendant_dead = _wait_for_pid_dead(descendant_pid, timeout_s=5.0)
    finally:
        # Never leave a long-running holder (or its tree) behind when an
        # assertion fires before the kill path ran.
        if holder.poll() is None:
            with contextlib.suppress(Exception):
                holder.kill()
                holder.wait(timeout=2.0)
        for pid in (worker_pid, descendant_pid):
            if pid <= 0:
                continue
            with contextlib.suppress(ProcessLookupError, OSError):
                os.kill(pid, 9)

    return _LivenessKillOutcome(
        cause=cause,
        verdict=verdict,
        worker_pid=worker_pid,
        descendant_pid=descendant_pid,
        worker_dead=worker_dead,
        descendant_dead=descendant_dead,
    )


#: Aging steps that age the worker's own fork out of the sampler's windowed
#: child-event history, so ``TreeActive`` stops masking the stall branch.
#: Two steps of 35 s put the fork at ``t = 35`` and a clean re-baseline at
#: ``t = 70``; the first drive sample then reads ``now = 70.x`` with a 30 s
#: evidence window, so the ``t = 35`` fork record is pruned while the ``t = 70``
#: record survives. Both records matter: pruning the fork record is what frees
#: the stall branch, and keeping the ``t = 70`` record is what keeps the CPU
#: rate window short enough to still read as "busy" in the no-progress proof.
#: Shared by the two causes whose branch sits behind the liveness sampler.
_AGE_STEPS: int = 2
_AGE_CLOCK_STEP_S: float = 35.0


def test_liveness_kill_stalled_reaps_worker_and_descendant(tmp_path: Path) -> None:
    """Proof (e1): a ``stalled`` kill reaps the whole tree, not just the root.

    The exec worker is silent from birth, so ``last_output_at`` stays ``None``
    and every sample scores 0.0 through ``R5`` (idle) alone. Pressure
    therefore accrues at the maximum unit rate and crosses the stall window
    (``TIMEOUT_MS = 800`` -> 0.8 s) within a few samples, which is exactly the
    shape criterion C3 names.
    """
    out = _run_liveness_kill_case(
        tmp_path,
        age_steps=_AGE_STEPS,
        age_clock_step_s=_AGE_CLOCK_STEP_S,
        drive_steps=6,
        drive_clock_step_s=0.25,
        drive_dt_s=0.25,
        # No real sleep: the idle worker burns no CPU, so the score stays a
        # clean 0.0 and the crossing lands within a couple of samples.
        drive_real_sleep_s=0.0,
        refresh_wait=True,
        timeout_ms=800,
        max_runtime_ms=3_600_000,
        spin=False,
    )

    assert out.cause == "stalled", f"expected 'stalled', got {out.cause!r}; {out.verdict!r}"
    assert out.worker_dead, f"Worker {out.worker_pid} was not reaped on a stalled kill"
    assert out.descendant_dead, (
        f"Descendant {out.descendant_pid} was not reaped on a stalled kill — "
        "tree cleanup incomplete"
    )


def test_liveness_kill_hard_cap_reaps_worker_and_descendant(tmp_path: Path) -> None:
    """Proof (e2): a ``hard_cap`` kill reaps the whole tree, not just the root.

    The ceiling is shrunk to 1 s through the public ``max_runtime_ms``
    argument (no timeout constant is touched), and the stall window is pushed
    out of reach so the hard-cap branch is provably the one that fires.
    """
    out = _run_liveness_kill_case(
        tmp_path,
        age_steps=0,
        age_clock_step_s=0.0,
        drive_steps=6,
        drive_clock_step_s=0.4,
        drive_dt_s=0.0,
        drive_real_sleep_s=0.0,
        refresh_wait=False,
        timeout_ms=900_000,
        max_runtime_ms=1_000,
        spin=False,
    )

    assert out.cause == "hard_cap", f"expected 'hard_cap', got {out.cause!r}; {out.verdict!r}"
    assert out.worker_dead, f"Worker {out.worker_pid} was not reaped on a hard_cap kill"
    assert out.descendant_dead, (
        f"Descendant {out.descendant_pid} was not reaped on a hard_cap kill — "
        "tree cleanup incomplete"
    )


def test_liveness_kill_abandoned_reaps_worker_and_descendant(tmp_path: Path) -> None:
    """Proof (e3): an ``abandoned`` kill reaps the whole tree, not just the root.

    Abandonment is purely a clock verdict ("nobody has called ``wait`` for
    ``EXEC_JOB_ABANDON_S``"), so one step past the window fires it. The stall
    and hard-cap branches are held out of reach by their own windows.
    """
    out = _run_liveness_kill_case(
        tmp_path,
        age_steps=0,
        age_clock_step_s=0.0,
        drive_steps=1,
        drive_clock_step_s=121.0,
        drive_dt_s=0.0,
        drive_real_sleep_s=0.0,
        # No ``wait()`` refresh: refreshing it is exactly what defers the
        # abandonment verdict.
        refresh_wait=False,
        timeout_ms=900_000,
        max_runtime_ms=900_000,
        spin=False,
    )

    assert out.cause == "abandoned", f"expected 'abandoned', got {out.cause!r}; {out.verdict!r}"
    assert out.worker_dead, f"Worker {out.worker_pid} was not reaped on an abandoned kill"
    assert out.descendant_dead, (
        f"Descendant {out.descendant_pid} was not reaped on an abandoned kill — "
        "tree cleanup incomplete"
    )


def test_liveness_kill_no_progress_reaps_worker_and_descendant(tmp_path: Path) -> None:
    """Proof (e4): a ``no_progress`` kill reaps the whole tree, not the root only.

    The worker is a real CPU burner, so ``CpuBusy`` saturates and ``R4``
    (silent compute) dominates every sample. ``R4`` is a SUSPICIOUS rule, so
    the suspicion clock accrues while stall pressure drains (``z4 = 0.6`` is
    above theta) — the only trace shape that can reach the 15-minute
    no-progress window. The suspicion clock is charged a full 300 s per sample
    while the fake clock advances 0.15 s per sample, which crosses the window
    in three samples without any 15-minute wait.
    """
    out = _run_liveness_kill_case(
        tmp_path,
        age_steps=_AGE_STEPS,
        age_clock_step_s=_AGE_CLOCK_STEP_S,
        drive_steps=4,
        drive_clock_step_s=0.10,
        drive_dt_s=300.0,
        # Real CPU has to accumulate for the sampler to measure a busy ratio,
        # so this phase sleeps for real while the fake clock advances the same
        # amount -- the ratio stays saturated without a long wait.
        drive_real_sleep_s=0.10,
        # ``wait()`` between steps refreshes the abandonment clock, so only the
        # no-progress branch stays reachable.
        refresh_wait=True,
        timeout_ms=3_600_000,
        max_runtime_ms=3_600_000,
        spin=True,
    )

    assert out.cause == "no_progress", f"expected 'no_progress', got {out.cause!r}; {out.verdict!r}"
    assert out.worker_dead, f"Worker {out.worker_pid} was not reaped on a no_progress kill"
    assert out.descendant_dead, (
        f"Descendant {out.descendant_pid} was not reaped on a no_progress kill — "
        "tree cleanup incomplete"
    )
