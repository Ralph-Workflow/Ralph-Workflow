"""R4-BANNER regression: the CLI child's listening banner is visible to the parent
without ``PYTHONUNBUFFERED`` being inherited.

The previous CLI spawn path was the only one that needed the parent's
``PYTHONUNBUFFERED=1`` injection: ``print(...)`` is block-buffered on a
non-tty stdout, so the banner (``Ralph MCP server listening on ...``)
would sit inside the child's pipe buffer until the next block flush
(N seconds later, when the buffer filled or the child exited). WU-B
moved the fix to the child itself: ``runtime.py`` reconfigures
``sys.stdout`` to line-buffered on the ``env_loaded`` path, and
``_fallback_standalone_server.py`` prints the banner with
``flush=True``. This test proves the banner reaches the parent's pipe
within 1.0 s of the child reaching the independent ``ready_to_serve`` stage,
even when the parent did NOT pre-set ``PYTHONUNBUFFERED``.

The test is ``subprocess_e2e`` because the bug lives in the cross-
process pipe buffering: an in-process test that monkeypatches stdout
cannot exercise the failure mode. It is on the default ``make test``
profile via the ``REQUIRED_AUTO_INTEGRATE_E2E_FILES`` registry so the
banner-visibility contract cannot rot silently.
"""

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
_READY_TIMEOUT_S = 5.0


def _bind_free_port() -> int:
    """Reserve a port on 127.0.0.1, then release it. Returns the port.

    The kernel may hand the same port to a sibling process between this
    call and the child's bind, but in practice (single-host test
    runner, no other ralph servers starting) the race does not fire.
    """
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
    """Drain ``stream`` until the banner prefix appears or the deadline elapses.

    Returns the accumulated bytes; the caller asserts on the substring.
    On a non-tty pipe, ``os.read`` is block-buffered; the
    ``selectors.DefaultSelector`` gives us a ``select(2)`` wait that
    honors the deadline instead of blocking forever, so we can prove
    the banner arrives inside the 1.0 s window the bug fixed.
    """
    chunks: list[bytes] = []
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        events = selector.select(timeout=remaining)
        if not events:
            # Deadline: either banner arrived, or it didn't.
            break
        for _key, _mask in events:
            try:
                chunk = os.read(stream.fileno(), 65536)
            except BlockingIOError:
                continue
            if not chunk:
                # EOF before banner: child exited (or pipe closed).
                return b"".join(chunks)
            chunks.append(chunk)
            if _BANNER_PREFIX in b"".join(chunks):
                return b"".join(chunks)
    return b"".join(chunks)


def test_cli_child_banner_visible_within_one_second(tmp_path: Path) -> None:
    """Spawn the real CLI child WITHOUT ``PYTHONUNBUFFERED`` and assert the
    ``Ralph MCP server listening on ...`` banner reaches the parent
    within 1.0 s of reaching the ready_to_serve stage while the child
    remains alive.

    Failure mode this guards against: a fully-buffered child stdout
    holds the banner inside the pipe buffer until either the buffer
    fills (kilobytes later) or the child exits. With the fix the
    banner is line-buffered + explicitly flushed, so the parent sees
    it the moment the child's serving boundary crosses.
    """
    port = _bind_free_port()
    workspace = tmp_path
    stage_id = str(uuid.uuid4())

    # Build a clean env: no PYTHONUNBUFFERED, no PYTHONIOENCODING, and
    # no RALPH_MCP_INHERITED_FD (this is a fresh CLI invocation, not a
    # bridge-managed respawn on an inherited FD). PYTHONHASHSEED=0
    # keeps the env deterministic for any code that hashes by accident.
    # Pass an explicit stage_id via RALPH_MCP_STAGE_ID so parent and
    # child address the same stage file.
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONUNBUFFERED", "PYTHONIOENCODING", "RALPH_MCP_INHERITED_FD"}
    }
    env["PYTHONHASHSEED"] = "0"
    env["RALPH_MCP_INHERITED_FD"] = ""
    env["RALPH_MCP_STAGE_ID"] = stage_id

    # The child is the real production CLI: ``python -m ralph.mcp.server``.
    # ``cwd=`` is the ralph-workflow project root so ``-m`` resolves the
    # ralph package; ``--workspace=tmp_path`` is the workspace the
    # runtime will read stage files from.
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
        # Step 1: Wait for independent serving signal: poll
        # read_last_stage(workspace, spawn_id=stage_id) == "ready_to_serve"
        # with a bounded timeout (5.0s).
        ready_deadline = time.monotonic() + _READY_TIMEOUT_S
        ready_reached = False
        while time.monotonic() < ready_deadline:
            if read_last_stage(workspace, spawn_id=stage_id) == "ready_to_serve":
                ready_reached = True
                break
            if process.poll() is not None:
                break
            time.sleep(0.01)

        assert ready_reached, (
            f"CLI child failed to reach 'ready_to_serve' stage within {_READY_TIMEOUT_S:.1f}s; "
            f"last_stage={read_last_stage(workspace, spawn_id=stage_id)!r}, "
            f"returncode={process.poll()}"
        )

        # Step 2: As soon as ready_to_serve is reached, start the 1.0s banner deadline.
        banner_deadline = time.monotonic() + _BANNER_TIMEOUT_S
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            accumulated = _read_until_banner(
                process.stdout,
                selector,
                banner_deadline,
            )

        # Step 3: Assert that _BANNER_PREFIX in accumulated.
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

        # Step 4: Assert that process.poll() is None when the banner is accepted
        # (child MUST be alive!).
        assert process.poll() is None, (
            f"CLI child was expected to be alive when banner was accepted, "
            f"but process exited with code {process.returncode}"
        )
    finally:
        # Retain bounded cleanup (process.terminate(), wait).
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
