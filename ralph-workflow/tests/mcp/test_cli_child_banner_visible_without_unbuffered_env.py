from __future__ import annotations

import os
import selectors
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import BinaryIO

import pytest

from ralph.mcp.server._start_stage_tracker import read_last_stage

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]

_BANNER_PREFIX = b"Ralph MCP server listening on "
_BANNER_TIMEOUT_S = 1.0
_READY_TIMEOUT_S = 15.0


def _bind_free_port() -> int:
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


def _read_until_banner(
    stream: BinaryIO,
    selector: selectors.DefaultSelector,
    deadline: float,
) -> bytes:
    chunks: list[bytes] = []
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        events = selector.select(timeout=remaining)
        if not events:
            break
        for _key, _mask in events:
            try:
                chunk = os.read(stream.fileno(), 65536)
            except BlockingIOError:
                continue
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            if _BANNER_PREFIX in b"".join(chunks):
                return b"".join(chunks)
    return b"".join(chunks)


def test_cli_child_banner_visible_within_one_second(tmp_path: Path) -> None:
    port = _bind_free_port()
    workspace = tmp_path
    stage_id = str(uuid.uuid4())

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONUNBUFFERED", "PYTHONIOENCODING", "RALPH_MCP_INHERITED_FD"}
    }
    env["PYTHONHASHSEED"] = "0"
    env["RALPH_MCP_STAGE_ID"] = stage_id

    ralph_workflow_root = Path(__file__).resolve().parents[2]
    argv = [
        sys.executable,
        "-m",
        "ralph.mcp.server",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--workspace",
        str(workspace),
    ]

    process = subprocess.Popen(
        argv,
        env=env,
        cwd=str(ralph_workflow_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    assert process.stdout is not None

    try:
        ready_deadline = time.monotonic() + _READY_TIMEOUT_S
        ready_reached = False
        while time.monotonic() < ready_deadline:
            if read_last_stage(workspace, spawn_id=stage_id) == "ready_to_serve":
                ready_reached = True
                break
            if process.poll() is not None:
                break
            time.sleep(0.025)

        assert ready_reached, (
            f"CLI child failed to reach 'ready_to_serve' stage within {_READY_TIMEOUT_S:.1f}s; "
            f"last_stage={read_last_stage(workspace, spawn_id=stage_id)!r}, "
            f"returncode={process.poll()}"
        )

        banner_deadline = time.monotonic() + _BANNER_TIMEOUT_S
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            accumulated = _read_until_banner(
                process.stdout,
                selector,
                banner_deadline,
            )

        assert _BANNER_PREFIX in accumulated, (
            "CLI child never emitted the 'Ralph MCP server listening on ' "
            f"banner within {_BANNER_TIMEOUT_S:.1f}s of ready_to_serve. The bug is "
            "back: the child is block-buffering stdout because "
            "``PYTHONUNBUFFERED`` was not inherited and the "
            "line_buffering reconfigure in runtime.py / the explicit "
            "flush=True in _fallback_standalone_server.py are not taking "
            f"effect. Accumulated stdout so far: {accumulated!r}; "
            f"argv was: {argv!r}; returncode={process.returncode}"
        )

        assert process.poll() is None, (
            f"CLI child was expected to be alive when banner was accepted, "
            f"but process exited with code {process.returncode}"
        )
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
