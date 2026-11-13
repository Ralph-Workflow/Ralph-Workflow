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
from ralph.process.manager import (
    ManagedProcess,
    ProcessTerminationIssuer,
    SpawnOptions,
    get_process_manager,
)

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(20),
]


_POLL_INTERVAL_S: float = 0.005


def _wait_for_pid_dead(pid: int, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(_POLL_INTERVAL_S)
        except ProcessLookupError:
            return True
        except OSError:
            return True
    return False


def _wait_for_sentinel(path: str, timeout_s: float = 15.0) -> int:
    sentinel = Path(path)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if sentinel.exists():
            try:
                with sentinel.open() as fh:
                    return int(fh.read().strip())
            except (OSError, ValueError):
                pass
        time.sleep(_POLL_INTERVAL_S)
    raise AssertionError(f"sentinel {path!r} never written within {timeout_s}s")


def _spawn_tree_script() -> str:
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
    worker_pid = _wait_for_sentinel(worker_file, timeout_s=15.0)
    descendant_pid = _wait_for_sentinel(descendant_file, timeout_s=15.0)
    assert worker_pid > 0
    assert descendant_pid > 0
    os.kill(worker_pid, 0)
    os.kill(descendant_pid, 0)

    child.terminate()

    cause_line = child.stdout.readline().strip()
    assert cause_line == "CAUSE session_end", f"Expected 'CAUSE session_end', got: {cause_line!r}"

    child.wait(timeout=5.0)

    worker_dead = _wait_for_pid_dead(worker_pid, timeout_s=3.0)
    descendant_dead = _wait_for_pid_dead(descendant_pid, timeout_s=3.0)
    assert worker_dead, f"Worker {worker_pid} not terminated on SIGTERM"
    assert descendant_dead, (
        f"Descendant {descendant_pid} not reaped on SIGTERM — tree cleanup incomplete"
    )


def test_owner_loss_watcher_kills_worker_and_descendant(
    tmp_path: object,
) -> None:
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
    worker_pid = _wait_for_sentinel(worker_file, timeout_s=15.0)
    descendant_pid = _wait_for_sentinel(descendant_file, timeout_s=15.0)
    child_pid = _wait_for_sentinel(child_pid_file, timeout_s=15.0)
    assert worker_pid > 0 and descendant_pid > 0 and child_pid > 0
    os.kill(child_pid, 0)
    os.kill(worker_pid, 0)
    os.kill(descendant_pid, 0)

    owner.kill()
    owner.wait(timeout=5.0)

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
    holder_pid_file = f"{tmp_path}/holder.pid"
    worker_file = f"{tmp_path}/worker.pid"

    holder_code = """
import os, sys, time
from ralph.mcp.tools._exec_jobs import ExecJobRegistry

cmd = ["sleep", "60"]
registry = ExecJobRegistry(auto_register_runtime_hooks=False)
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
    holder_pid = _wait_for_sentinel(holder_pid_file, timeout_s=15.0)
    worker_pid = _wait_for_sentinel(worker_file, timeout_s=15.0)
    assert holder_pid > 0
    assert worker_pid > 0

    os.kill(holder_pid, 0)
    os.kill(worker_pid, 0)

    holder.kill()
    holder.wait(timeout=2.0)

    worker_dead = _wait_for_pid_dead(worker_pid, timeout_s=1.5)
    assert worker_dead, (
        f"Worker {worker_pid} was not reaped on parent SIGKILL — "
        "PR_SET_PDEATHSIG installed by ralph.process.manager did not fire"
    )


@_LINUX_ONLY
def test_worker_thread_sigkill_kills_exec_job_tree_and_its_descendant(
    tmp_path: object,
) -> None:
    holder_pid_file = f"{tmp_path}/holder.pid"
    supervisor_pid_file = f"{tmp_path}/supervisor.pid"
    worker_pid_file = f"{tmp_path}/worker.pid"
    descendant_pid_file = f"{tmp_path}/descendant.pid"

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
        "_REGISTRY = ExecJobRegistry(auto_register_runtime_hooks=False)\n"
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
    holder_pid = 0
    supervisor_pid = 0
    worker_pid = 0
    descendant_pid = 0
    try:
        holder_pid = _wait_for_sentinel(holder_pid_file, timeout_s=15.0)
        supervisor_pid = _wait_for_sentinel(supervisor_pid_file, timeout_s=15.0)
        worker_pid = _wait_for_sentinel(worker_pid_file, timeout_s=15.0)
        descendant_pid = _wait_for_sentinel(descendant_pid_file, timeout_s=15.0)
        assert holder_pid > 0
        assert supervisor_pid > 0
        assert worker_pid > 0
        assert descendant_pid > 0
        assert supervisor_pid != worker_pid, (
            f"supervisor_pid ({supervisor_pid}) must not collide with worker_pid ({worker_pid})"
        )

        os.kill(holder_pid, 0)
        os.kill(supervisor_pid, 0)
        os.kill(worker_pid, 0)
        os.kill(descendant_pid, 0)

        holder.kill()
        holder.wait(timeout=2.0)

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
    graceful = tmp_path / "graceful"
    ready = tmp_path / "ready"
    command = [sys.executable, "-c", _GRACEFUL_WORKER, str(graceful), str(ready)]
    options = SpawnOptions(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    handle = _on_worker_thread(lambda: get_process_manager().spawn(command, options))
    assert isinstance(handle, ManagedProcess)
    _wait_for_sentinel_text(ready)

    handle.terminate(grace_period_s=5.0, issuer=ProcessTerminationIssuer.OPERATOR)

    assert graceful.read_text(encoding="utf-8") == "graceful"


def _wait_for_sentinel_text(path: Path, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"{path} never written within {timeout_s}s")
        time.sleep(_POLL_INTERVAL_S)


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


_AGE_STEPS: int = 2
_AGE_CLOCK_STEP_S: float = 35.0


def test_liveness_kill_stalled_reaps_worker_and_descendant(tmp_path: Path) -> None:
    out = _run_liveness_kill_case(
        tmp_path,
        age_steps=_AGE_STEPS,
        age_clock_step_s=_AGE_CLOCK_STEP_S,
        drive_steps=2,
        drive_clock_step_s=0.25,
        drive_dt_s=0.9,
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
    out = _run_liveness_kill_case(
        tmp_path,
        age_steps=0,
        age_clock_step_s=0.0,
        drive_steps=2,
        drive_clock_step_s=0.5,
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
    out = _run_liveness_kill_case(
        tmp_path,
        age_steps=0,
        age_clock_step_s=0.0,
        drive_steps=1,
        drive_clock_step_s=121.0,
        drive_dt_s=0.0,
        drive_real_sleep_s=0.0,
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
    out = _run_liveness_kill_case(
        tmp_path,
        age_steps=_AGE_STEPS,
        age_clock_step_s=_AGE_CLOCK_STEP_S,
        drive_steps=2,
        drive_clock_step_s=0.10,
        drive_dt_s=900.0,
        drive_real_sleep_s=0.02,
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
