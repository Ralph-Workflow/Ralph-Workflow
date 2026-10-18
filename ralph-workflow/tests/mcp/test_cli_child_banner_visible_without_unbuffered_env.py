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
within 1.0 s of stdout becoming readable, even when the parent did
NOT pre-set ``PYTHONUNBUFFERED``.

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
from pathlib import Path

import pytest

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]

_BANNER_PREFIX = b"Ralph MCP server listening on "
_BANNER_TIMEOUT_S = 1.0
_OVERALL_BUDGET_S = 5.0
_CHILD_STARTUP_BUDGET_S = 10.0


def _bind_free_port() -> int:
    """Reserve a port on 127.0.0.1, then release it. Returns the port.

    The kernel may hand the same port to a sibling process between this
    call and the child's bind, but in practice (single-host test
    runner, no other ralph servers starting) the race does not fire.
    """
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
    finally:
        probe.close()


def _wait_first_event(
    stream,
    selector: selectors.DefaultSelector,
    deadline: float,
) -> bytes:
    """Wait for the child's stdout to first become readable.

    The child takes a few hundred ms to import the ralph package and
    build the server before any byte lands on stdout. The 1.0 s banner
    budget starts FROM this first readable event, not from the
    ``Popen`` call.
    """
    chunks: list[bytes] = []
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        events = selector.select(timeout=remaining)
        if not events:
            continue
        for _key, _mask in events:
            try:
                chunk = os.read(stream.fileno(), 65536)
            except BlockingIOError:
                continue
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            return b"".join(chunks)
    return b"".join(chunks)


def _read_until_banner(
    stream,
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
    within 1.0 s of stdout becoming readable.

    Failure mode this guards against: a fully-buffered child stdout
    holds the banner inside the pipe buffer until either the buffer
    fills (kilobytes later) or the child exits. With the fix the
    banner is line-buffered + explicitly flushed, so the parent sees
    it the moment the child's serving boundary crosses.
    """
    port = _bind_free_port()
    workspace = tmp_path

    # Build a clean env: no PYTHONUNBUFFERED, no PYTHONIOENCODING, and
    # no RALPH_MCP_INHERITED_FD (this is a fresh CLI invocation, not a
    # bridge-managed respawn on an inherited FD). PYTHONHASHSEED=0
    # keeps the env deterministic for any code that hashes by accident.
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONUNBUFFERED", "PYTHONIOENCODING", "RALPH_MCP_INHERITED_FD"}
    }
    env["PYTHONHASHSEED"] = "0"
    env["RALPH_MCP_INHERITED_FD"] = ""

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

    banner_found = False
    initial_chunk = b""
    accumulated = b""
    overall_deadline = time.monotonic() + _OVERALL_BUDGET_S

    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            # Step 1: wait for the child's stdout to first become
            # readable. This absorbs the cold-start cost (imports,
            # stage recording, server build) so the 1.0 s banner
            # budget measures ONLY the buffering behaviour we are
            # regressing on, not child-startup latency.
            startup_deadline = time.monotonic() + _CHILD_STARTUP_BUDGET_S
            initial_chunk = _wait_first_event(
                process.stdout,
                selector,
                startup_deadline,
            )
            if initial_chunk:
                accumulated = initial_chunk
                if _BANNER_PREFIX in accumulated:
                    banner_found = True

            # Step 2: now that the pipe is open, give the banner
            # exactly 1.0 s to appear. A buffered child would sit
            # silent until the buffer fills; a fixed child emits
            # the banner the moment the serve_forever boundary
            # crosses.
            if not banner_found:
                banner_deadline = time.monotonic() + _BANNER_TIMEOUT_S
                more = _read_until_banner(
                    process.stdout,
                    selector,
                    banner_deadline,
                )
                accumulated = initial_chunk + more
                if _BANNER_PREFIX in accumulated:
                    banner_found = True
        finally:
            if not banner_found and process.poll() is not None and process.stdout is not None:
                # Drain any remaining bytes (e.g. late banner after
                # the child exits and the buffer is flushed by the
                # kernel) so the failure message is informative.
                try:
                    rest = process.stdout.read()
                except Exception:
                    rest = b""
                if rest:
                    accumulated += rest
                    if _BANNER_PREFIX in accumulated:
                        banner_found = True

    try:
        # If the banner never arrived, surface the child's stderr so
        # the failure message is actionable (a clean run only ever
        # produces the banner; an error produces a stack trace on
        # stderr that the parent cannot otherwise see once stdout
        # is buffered).
        assert banner_found, (
            "CLI child never emitted the 'Ralph MCP server listening on ' "
            "banner within 1.0s of stdout becoming readable. The bug is "
            "back: the child is block-buffering stdout because "
            "``PYTHONUNBUFFERED`` was not inherited and the "
            "line_buffering reconfigure in runtime.py / the explicit "
            "flush=True in _fallback_standalone_server.py are not taking "
            f"effect. Accumulated stdout so far: {accumulated!r}; "
            f"argv was: {argv!r}; returncode={process.returncode}"
        )

        # Bound the absolute wall-clock too: a banner at 0.95s inside a
        # 4.5s test is fine; a banner at 4.9s means the child was just
        # slow, not the buffered-output bug.
        assert time.monotonic() < overall_deadline, (
            f"banner arrived but overall test budget (>{_OVERALL_BUDGET_S:.1f}s) was exceeded"
        )
    finally:
        # Always reap the child so a CI hang cannot leak a zombie.
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
