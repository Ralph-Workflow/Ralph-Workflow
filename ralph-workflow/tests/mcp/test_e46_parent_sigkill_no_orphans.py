"""Pins E4.6: parent SIGKILLed -> PDEATHSIG/EOF-watch ensures zero orphan processes and port is freed.

Catalogue requirement E4.6:
- Ralph dies hard (SIGKILL, OOM): The MCP children, the candidate and their
  grandchildren must exit on their own and not keep the port.
- Required: Use PR_SET_PDEATHSIG or an EOF watch / parent-watch on a parent-held
  condition (exit_when_owner_exits watching RALPH_MCP_OWNER_PID), and test it.
  Zero orphan processes remain and the port is freed.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ralph.mcp.protocol.env import MCP_OWNER_PID_ENV
from ralph.mcp.server.runtime import exit_when_owner_exits

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def test_parent_sigkilled_child_exits_and_frees_port(tmp_path: Path) -> None:
    """When the parent Ralph process is abruptly killed with SIGKILL, the child terminates and frees the port."""
    ralph_root = Path(__file__).resolve().parents[2]

    # Child code: binds a listening socket, sets up exit_when_owner_exits, reports PID and port, then waits
    child_script = tmp_path / "mcp_child.py"
    child_script.write_text(
        "\n".join(
            [
                "import os, sys, socket, time",
                f"sys.path.insert(0, {str(ralph_root)!r})",
                "from ralph.mcp.server.runtime import exit_when_owner_exits",
                "# Install the parent-exit watchdog",
                "watcher = exit_when_owner_exits(os.environ)",
                "sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)",
                "sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)",
                "sock.bind(('127.0.0.1', 0))",
                "sock.listen(128)",
                "port = sock.getsockname()[1]",
                "# Emit READY with child pid and port",
                "print(f'CHILD_READY {os.getpid()} {port}', flush=True)",
                "# Keep serving / holding socket until parent dies and watcher calls os._exit",
                "while True:",
                "    time.sleep(0.1)",
            ]
        ),
        encoding="utf-8",
    )

    owner_env_name = str(MCP_OWNER_PID_ENV)
    # Parent code: launches child with MCP_OWNER_PID_ENV set to its own PID, forwards READY line, then waits
    parent_script = tmp_path / "ralph_parent.py"
    parent_script.write_text(
        "\n".join(
            [
                "import os, sys, subprocess",
                "child_env = dict(os.environ)",
                f"child_env[{owner_env_name!r}] = str(os.getpid())",
                f"child_proc = subprocess.Popen([sys.executable, {str(child_script)!r}], env=child_env, stdout=subprocess.PIPE, text=True)",
                "line = child_proc.stdout.readline()",
                "print(f'PARENT_PID {os.getpid()} {line.strip()}', flush=True)",
                "# Parent sleeps indefinitely until test runner SIGKILLs it",
                "child_proc.wait()",
            ]
        ),
        encoding="utf-8",
    )

    # Spawn the parent process
    parent_proc = subprocess.Popen(
        [sys.executable, str(parent_script)],
        stdout=subprocess.PIPE,
        text=True,
    )

    try:
        assert parent_proc.stdout is not None
        line = parent_proc.stdout.readline().strip()
        # Format: PARENT_PID <parent_pid> CHILD_READY <child_pid> <port>
        parts = line.split()
        assert len(parts) == 5, f"Unexpected parent banner: {line!r}"
        assert parts[0] == "PARENT_PID"
        assert parts[2] == "CHILD_READY"

        parent_pid = int(parts[1])
        child_pid = int(parts[3])
        port = int(parts[4])

        # 1. Verify child is alive and port is accepting connections
        os.kill(child_pid, 0)
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as test_conn:
            assert test_conn is not None

        # 2. Abruptly kill the parent process with SIGKILL (simulating OOM or hard crash)
        os.kill(parent_pid, signal.SIGKILL)
        parent_proc.wait(timeout=2.0)

        # 3. Watch for child process to terminate automatically on detecting parent death
        deadline = time.monotonic() + 3.0
        child_exited = False
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
                time.sleep(0.05)
            except ProcessLookupError:
                child_exited = True
                break

        assert child_exited is True, (
            f"Child process {child_pid} remained orphaned after parent {parent_pid} was SIGKILLed!"
        )

        # 4. Invariant: The port is freed and can be bound immediately
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as rebound_sock:
            rebound_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            rebound_sock.bind(("127.0.0.1", port))

    finally:
        # Leak cleanup
        with contextlib.suppress(Exception):
            if parent_proc.poll() is None:
                parent_proc.kill()
                parent_proc.wait(timeout=1.0)
        with contextlib.suppress(Exception):
            os.kill(child_pid, signal.SIGKILL)


def test_exit_when_owner_exits_reparenting_detection() -> None:
    """Watcher thread detects reparenting away from owner and triggers process exit."""
    parents = iter([500, 500, 1])  # 1 simulates init / subreaper reparenting
    exits: list[int] = []
    env = {MCP_OWNER_PID_ENV: "500"}

    watcher = exit_when_owner_exits(
        env,
        get_parent_pid=lambda: next(parents),
        sleep=lambda _s: None,
        exit_process=exits.append,
    )

    assert watcher is not None
    watcher.join(timeout=3.0)

    # Invariant: Exited with code 0 on detecting reparenting
    assert exits == [0]
    assert MCP_OWNER_PID_ENV not in env
