"""Pins E3.3: listening fd is not inherited by non-MCP real children.

Catalogue requirement E3.3:
- Listening fd leaks into other children: The parent's listening socket must be
  non-inheritable for every other spawn (agents, git, upstreams, sub-agents),
  because Python only passes it where pass_fds names it.
- Required: a test that a non-MCP child holds no copy. Otherwise an agent
  process could keep the port bound after bridge shutdown, or accept
  connections itself. Inspect /proc/<pid>/fd of the child process.
"""

from __future__ import annotations

import contextlib
import os
import socket
import sys
from pathlib import Path

import pytest

# Pre-import workspace to prevent circular import between process.manager and workspace
from ralph import workspace as _workspace
from ralph.mcp.protocol.env import MCP_INHERITED_FD_ENV
from ralph.mcp.server import lifecycle
from ralph.process.manager import SpawnOptions, get_process_manager

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]
_ = _workspace


def _is_linux() -> bool:
    return sys.platform.startswith("linux") and Path("/proc").is_dir()


def _get_process_open_inodes(pid: int) -> set[int]:
    """Inspect /proc/<pid>/fd and collect inode numbers of all open file descriptors."""
    fd_dir = Path(f"/proc/{pid}/fd")
    if not fd_dir.is_dir():
        return set()
    inodes: set[int] = set()
    for link in fd_dir.iterdir():
        try:
            inodes.add(link.stat().st_ino)
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return inodes


def test_non_mcp_real_child_does_not_inherit_listening_fd(tmp_path: Path) -> None:
    """Non-MCP real child process holds NO copy of the parent's listening socket fd."""
    if not _is_linux():
        pytest.skip("Test requires Linux /proc/<pid>/fd inspection")

    # Parent creates and binds a real listening socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)

    listening_fd = sock.fileno()
    sock_ino = os.fstat(listening_fd).st_ino
    port = sock.getsockname()[1]

    pm = get_process_manager()
    # Spawn a non-MCP child process (e.g. agent tool, git, sub-process)
    child = pm.spawn(
        [sys.executable, "-c", "import time; time.sleep(3)"],
        SpawnOptions(cwd=str(tmp_path), label="e33-non-mcp-child"),
    )

    try:
        # Collect inodes held by the non-MCP child
        child_inodes = _get_process_open_inodes(child.pid)

        # Invariant: Socket inode is NOT held by the child
        assert sock_ino not in child_inodes, (
            f"Listening socket inode {sock_ino} leaked into child PID {child.pid}!"
        )

        # Verify specific FD number is also not pointing to our socket
        child_fd_link = Path(f"/proc/{child.pid}/fd/{listening_fd}")
        if child_fd_link.exists():
            assert child_fd_link.stat().st_ino != sock_ino
    finally:
        with contextlib.suppress(Exception):
            child.kill()
            child.wait(timeout=2)
        sock.close()

    # Leak assertion: Child is dead and port is freed
    assert child.poll() is not None
    # Re-binding to the port succeeds without collision
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as verify_sock:
        verify_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        verify_sock.bind(("127.0.0.1", port))


def test_pass_fds_explicitly_passes_socket_to_child(tmp_path: Path) -> None:
    """When pass_fds explicitly names the fd, the child process inherits it."""
    if not _is_linux():
        pytest.skip("Test requires Linux /proc/<pid>/fd inspection")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)

    listening_fd = sock.fileno()
    sock_ino = os.fstat(listening_fd).st_ino
    port = sock.getsockname()[1]

    pm = get_process_manager()
    # Spawn child with pass_fds naming the listening socket fd
    child = pm.spawn(
        [sys.executable, "-c", "import time; time.sleep(3)"],
        SpawnOptions(
            cwd=str(tmp_path),
            pass_fds=(listening_fd,),
            label="e33-passed-fd-child",
        ),
    )

    try:
        child_inodes = _get_process_open_inodes(child.pid)
        # Invariant: Socket inode IS held by the child
        assert sock_ino in child_inodes, (
            f"Socket inode {sock_ino} was expected in child PID {child.pid} but not found!"
        )
    finally:
        with contextlib.suppress(Exception):
            child.kill()
            child.wait(timeout=2)
        sock.close()

    # Leak assertion: Child is dead and port can be bound
    assert child.poll() is not None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as verify_sock:
        verify_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        verify_sock.bind(("127.0.0.1", port))


def test_subprocess_env_omits_mcp_inherited_fd_env(tmp_path: Path) -> None:
    """_subprocess_env must never ambiently set MCP_INHERITED_FD_ENV for non-MCP children."""
    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")

    env = lifecycle._subprocess_env(session_file)
    assert MCP_INHERITED_FD_ENV not in env
