from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import psutil
import pytest

from ralph.interrupt.controller import INTERRUPT_EXIT_CODE

pytestmark = pytest.mark.subprocess_e2e

PYTHON = sys.executable
REPO_ROOT = Path(__file__).resolve().parents[2]
_STUBBORN_CHILD = (
    "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); signal.pause()"
)


def _pid_gone(pid: int, timeout_s: float = 1.5) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return True
        time.sleep(0.02)
    return not psutil.pid_exists(pid)


@pytest.mark.timeout_seconds(5)
def test_live_sigint_gracefully_terminates_runner_and_tracked_child(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result_path = tmp_path / "interrupt-result.json"
    script = textwrap.dedent(
        """
        import asyncio
        import json
        import os
        import signal
        import sys
        import threading
        from pathlib import Path

        import ralph.process.manager as _mgr
        from ralph.interrupt.asyncio_bridge import SignalBridge, install_signal_handlers
        from ralph.process.manager import ProcessManager, ProcessManagerPolicy, get_process_manager

        workspace = Path(sys.argv[1])
        result_path = Path(sys.argv[2])
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / ".agent").mkdir(exist_ok=True)

        pm = ProcessManager(
            policy=ProcessManagerPolicy(
                default_grace_period_s=0.1,
                kill_followup_timeout_s=0.2,
                log_events=False,
            )
        )

        async def main() -> None:
            bridge = SignalBridge()
            loop = asyncio.get_running_loop()
            root_task = asyncio.current_task()
            assert root_task is not None
            install_signal_handlers(loop, root_task, bridge)

            handle = get_process_manager().spawn(
                [sys.executable, "-c", "import signal; signal.pause()"],
                label="invoke:fake-agent",
            )
            child_pid = handle.record.pid
            threading.Timer(0.01, lambda: os.kill(os.getpid(), signal.SIGINT)).start()

            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                pass

            result_path.write_text(
                json.dumps({"runner_exit_code": 130, "child_pid": child_pid}),
                encoding="utf-8",
            )

        original_singleton = _mgr._singleton
        _mgr._singleton = pm
        try:
            asyncio.run(main())
        finally:
            _mgr._singleton = original_singleton
        """
    )

    completed = subprocess.run(
        [PYTHON, "-c", script, str(workspace), str(result_path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=6,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["runner_exit_code"] == INTERRUPT_EXIT_CODE
    assert _pid_gone(payload["child_pid"]), (
        f"Tracked PID {payload['child_pid']} must be gone after live SIGINT handling"
    )


@pytest.mark.timeout_seconds(5)
def test_second_live_sigint_force_kills_stubborn_child(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace-second"
    workspace.mkdir()
    script = textwrap.dedent(
        f"""
        import asyncio
        import os
        import signal
        import sys
        import threading
        from pathlib import Path

        import ralph.process.manager as _mgr
        from ralph.interrupt.asyncio_bridge import SignalBridge, install_signal_handlers
        from ralph.process.manager import ProcessManager, ProcessManagerPolicy, get_process_manager

        workspace = Path(sys.argv[1])
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / ".agent").mkdir(exist_ok=True)
        stubborn_child = {_STUBBORN_CHILD!r}

        pm = ProcessManager(
            policy=ProcessManagerPolicy(
                default_grace_period_s=0.1,
                kill_followup_timeout_s=0.2,
                log_events=False,
            )
        )

        async def main() -> None:
            bridge = SignalBridge()
            loop = asyncio.get_running_loop()
            root_task = asyncio.current_task()
            assert root_task is not None
            install_signal_handlers(loop, root_task, bridge)

            handle = get_process_manager().spawn(
                [sys.executable, "-c", stubborn_child],
                label="invoke:fake-agent",
            )
            print(f"CHILD_PID={{handle.record.pid}}", flush=True)
            threading.Timer(0.05, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
            threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGINT)).start()

            for _ in range(40):
                try:
                    await asyncio.sleep(0.05)
                except asyncio.CancelledError:
                    pass

        original_singleton = _mgr._singleton
        _mgr._singleton = pm
        try:
            asyncio.run(main())
        finally:
            _mgr._singleton = original_singleton
        """
    )

    completed = subprocess.run(
        [PYTHON, "-c", script, str(workspace)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=6,
        check=False,
    )

    child_pid_line = next(
        (line for line in completed.stdout.splitlines() if line.startswith("CHILD_PID=")),
        None,
    )
    assert child_pid_line is not None, completed.stdout
    child_pid = int(child_pid_line.split("=", 1)[1])

    assert completed.returncode == INTERRUPT_EXIT_CODE, completed.stderr
    assert _pid_gone(child_pid), (
        f"Tracked PID {child_pid} must be gone after second-SIGINT forced termination"
    )
