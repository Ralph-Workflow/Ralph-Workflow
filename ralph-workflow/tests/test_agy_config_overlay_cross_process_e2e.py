"""Cross-process E2E proof for the AGY MCP config overlay lock (plan S-4).

``agy_workspace_mcp_endpoint`` mutates two GLOBAL config files shared by
every AGY launch on the machine. A process-local ``threading.Lock`` cannot
serialize two INDEPENDENT Ralph processes racing those paths, so the
overlay now also takes a bounded advisory ``fcntl.flock`` sidecar lock.
These tests launch real subprocesses against the same temporary config
paths and prove:

* bounded contention -- a second process waits for the first, then
  proceeds with its own isolated staging (no torn JSON, no stale Ralph
  endpoint), and both processes restore their own original bytes;
* fail-closed timeout -- a process that cannot acquire the lock inside
  the deadline raises ``AgyMcpConfigLockTimeoutError`` and leaves both
  config files byte-identical to their pre-run contents;
* exception cleanup -- a failure inside the overlay body still restores
  both files exactly.

Marked ``subprocess_e2e``: real subprocess spawning is permitted here and
forbidden in the default suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.subprocess_e2e,
    # Each test launches one or two short-lived child processes; the
    # longest child holds the overlay lock for 3 s, so 20 s per test is
    # far beyond legitimate runtime while still bounding a hang.
    pytest.mark.timeout_seconds(20),
]

#: Per-subprocess hard cap. Each child only opens the overlay context
#: (milliseconds of real work) or waits on a short injected timeout, so
#: 30 s is far beyond legitimate runtime while still bounding a hang.
_CHILD_TIMEOUT_SECONDS = 30.0

#: Path to the checked-in child overlay helper. The helper is a real file
#: (not an inline ``-c`` program) so the child gets a clean module import
#: environment and so the program is reviewable in one place.
_CHILD_HELPER = str(Path(__file__).parent / "_support" / "_agy_config_overlay_child.py")


def _child_env() -> dict[str, str]:
    """Return an environment that pins the child to THIS worktree's package.

    The ambient shell ``PYTHONPATH`` points at a DIFFERENT checkout's
    project root (the harness's outer editable install), and that
    checkout's installed ``sitecustomize`` shim force-moves ITS OWN
    project root to the FRONT of ``sys.path`` at interpreter startup --
    ahead of any ``PYTHONPATH`` we could set. So ``PYTHONPATH`` alone
    cannot make a bare child ``python`` resolve ``import ralph`` to THIS
    worktree's package; the child would silently import the outer
    checkout's (possibly older) ``ralph.mcp.transport.agy`` and the lock
    behavior under test would not exist there.

    The override below (``sys.path.insert(0, <this repo root>)`` in the
    child helper, BEFORE ``import ralph``) is the only ordering that
    survives the shim, because the helper's own ``sys.path`` mutation
    runs AFTER ``sitecustomize``. ``PYTHONPATH`` is still set to this
    repo root for defense in depth, matching the ``_build_live_env``
    pattern in ``tests/test_agy_live_regression.py``.
    """
    env = dict(os.environ)
    repo_root = Path(__file__).resolve().parent.parent
    env["PYTHONPATH"] = str(repo_root)
    # Hand the repo root to the child helper explicitly so its startup
    # ``sys.path.insert(0, ...)`` cannot be reordered by the outer
    # checkout's ``sitecustomize`` shim.
    env["RALPH_AGY_OVERLAY_CHILD_REPO_ROOT"] = str(repo_root)
    return env


def _spawn_child(
    primary: Path,
    secondary: Path,
    *,
    hold_seconds: float,
    timeout_seconds: float,
) -> subprocess.Popen[str]:
    """Spawn (not wait on) one overlay child against the shared config paths."""
    return subprocess.Popen(  # subprocess_e2e: fixed argv, no shell
        [
            sys.executable,
            _CHILD_HELPER,
            str(primary),
            str(secondary),
            str(hold_seconds),
            str(timeout_seconds),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_child_env(),
    )


def _wait_staged(child: subprocess.Popen[str], *, timeout_seconds: float = 15.0) -> None:
    """Block until ``child`` flushes its ``STAGED`` readiness line.

    Bounded by ``timeout_seconds`` so a child that crashes before staging
    fails the parent fast instead of hanging the suite.
    """
    assert child.stdout is not None
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        line = child.stdout.readline()
        if not line:
            break
        if line.strip() == "STAGED":
            return
    child.kill()
    child.wait()
    raise AssertionError(f"child did not stage in time (rc={child.returncode})")


def _run_child(
    primary: Path,
    secondary: Path,
    *,
    hold_seconds: float,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    """Launch one overlay child process against the shared config paths."""
    return subprocess.run(  # subprocess_e2e: fixed argv, no shell
        [
            sys.executable,
            _CHILD_HELPER,
            str(primary),
            str(secondary),
            str(hold_seconds),
            str(timeout_seconds),
        ],
        capture_output=True,
        text=True,
        timeout=_CHILD_TIMEOUT_SECONDS,
        check=False,
        env=_child_env(),
    )


def _seed_configs(tmp_path: Path) -> tuple[Path, Path, bytes, bytes]:
    """Create both global config paths with distinct pre-existing bytes."""
    primary = tmp_path / "gemini" / "mcp_config.json"
    secondary = tmp_path / "gemini-config" / "mcp_config.json"
    primary.parent.mkdir(parents=True)
    secondary.parent.mkdir(parents=True)
    primary_bytes = json.dumps({"mcpServers": {"upstream-a": {"serverUrl": "http://a"}}}).encode()
    secondary_bytes = json.dumps({"mcpServers": {"upstream-b": {"serverUrl": "http://b"}}}).encode()
    primary.write_bytes(primary_bytes)
    secondary.write_bytes(secondary_bytes)
    return primary, secondary, primary_bytes, secondary_bytes


def test_independent_processes_serialize_and_restore_byte_exact(tmp_path: Path) -> None:
    """Two racing processes serialize; each sees its own staging; both restore."""
    primary, secondary, primary_bytes, secondary_bytes = _seed_configs(tmp_path)

    # Process A holds the overlay long enough that B must wait on the lock.
    holder = _spawn_child(primary, secondary, hold_seconds=1.5, timeout_seconds=10.0)
    try:
        # Wait until A has provably staged (its stdout flushes "STAGED").
        _wait_staged(holder)

        # B starts while A holds the lock; it must block, then proceed.
        waiter = _run_child(
            primary, secondary, hold_seconds=0.0, timeout_seconds=10.0
        )
        assert waiter.returncode == 0, waiter.stderr
        assert "STAGED" in waiter.stdout
        assert "DONE" in waiter.stdout
    finally:
        holder_out, holder_err = holder.communicate(timeout=_CHILD_TIMEOUT_SECONDS)
    assert holder.returncode == 0, holder_err
    assert "DONE" in holder_out

    # After BOTH processes exit, each restored its own pre-existing bytes.
    assert primary.read_bytes() == primary_bytes
    assert secondary.read_bytes() == secondary_bytes
    # The staged content B saw must have been valid JSON with the Ralph
    # endpoint and must not leak upstream-a into upstream-b's file.
    assert json.loads(primary.read_text(encoding="utf-8"))["mcpServers"]["upstream-a"]


def test_lock_timeout_fails_closed_and_preserves_configs(tmp_path: Path) -> None:
    """A process that cannot acquire the lock in time fails closed."""
    primary, secondary, primary_bytes, secondary_bytes = _seed_configs(tmp_path)

    holder = _spawn_child(primary, secondary, hold_seconds=3.0, timeout_seconds=10.0)
    try:
        _wait_staged(holder)

        # B's timeout (0.5s) is far shorter than A's hold (3s): B must
        # fail closed with the lock-timeout signal, not race A.
        waiter = _run_child(
            primary, secondary, hold_seconds=0.0, timeout_seconds=0.5
        )
        assert waiter.returncode == 3, (waiter.returncode, waiter.stdout, waiter.stderr)
        assert "LOCK_TIMEOUT" in waiter.stdout
        # B never staged its own endpoint over A's active overlay.
        current = json.loads(primary.read_text(encoding="utf-8"))
        assert "ralph" in current["mcpServers"]  # still A's staged payload
    finally:
        holder_out, holder_err = holder.communicate(timeout=_CHILD_TIMEOUT_SECONDS)
    assert holder.returncode == 0, holder_err
    assert "DONE" in holder_out

    # A's exit restored its originals; B's failed attempt never touched them.
    assert primary.read_bytes() == primary_bytes
    assert secondary.read_bytes() == secondary_bytes


def test_overlay_exception_still_restores_both_configs(tmp_path: Path) -> None:
    """An injected failure inside the overlay body restores byte-exact."""
    primary, secondary, primary_bytes, secondary_bytes = _seed_configs(tmp_path)

    # Reuse the checked-in child helper's exception branch: pass a
    # negative hold value to signal "raise inside the overlay body".
    result = subprocess.run(  # subprocess_e2e: fixed argv, no shell
        [
            sys.executable,
            _CHILD_HELPER,
            "--raise-inside",
            str(primary),
            str(secondary),
            "0",
            "10.0",
        ],
        capture_output=True,
        text=True,
        timeout=_CHILD_TIMEOUT_SECONDS,
        check=False,
        env=_child_env(),
    )
    assert result.returncode == 0, result.stderr
    assert "FAILED_AND_RESTORED" in result.stdout
    assert primary.read_bytes() == primary_bytes
    assert secondary.read_bytes() == secondary_bytes
