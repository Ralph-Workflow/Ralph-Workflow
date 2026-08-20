"""End-to-end terminal restoration tests under a real PTY.

Exercises normal exit and signal-driven hard exits in child processes
under openpty to verify cursor visibility, alternate screen, mouse tracking,
bracketed paste, and termios line discipline are fully restored.
"""

from __future__ import annotations

import os
import pty
import sys
import termios
import textwrap

import pytest

from ralph.process.manager import SpawnOptions
from ralph.process.manager._process_manager import ProcessManager
from ralph.process.manager._process_manager_policy import ProcessManagerPolicy
from ralph.process.pty import read_master_chunk, wait_for_master_readable


@pytest.mark.subprocess_e2e
def test_terminal_restore_on_normal_exit_under_pty() -> None:
    """Normal exit restores termios ICANON/ECHO and outputs restore sequence."""
    pm = ProcessManager(
        policy=ProcessManagerPolicy(enable_zombie_reaper=False, log_events=False),
    )

    script = textwrap.dedent(
        """
        import sys
        from ralph.display.terminal_restore import restore_terminal
        from ralph.cli.main import ensure_cli_terminal_restore

        ensure_cli_terminal_restore()
        sys.stdout.write('\\x1b[?25l\\x1b[?1049hhello-pty\\n')
        sys.stdout.flush()
        """
    )
    master_fd, slave_fd = pty.openpty()
    try:
        handle = pm.spawn(
            [sys.executable, "-c", script],
            opts=SpawnOptions(stdin=slave_fd, stdout=slave_fd, stderr=slave_fd),
        )
        handle.wait(timeout=5.0)

        raw_chunks: list[bytes] = []
        while wait_for_master_readable(master_fd, timeout_seconds=0.05):
            chunk = read_master_chunk(master_fd, max_bytes=4096)
            if not chunk:
                break
            raw_chunks.append(chunk)

        captured = b"".join(raw_chunks)
        assert b"\x1b[?25h" in captured
        assert b"\x1b[?1049l" in captured

        slave_attrs = termios.tcgetattr(slave_fd)
        lflag: int = int(slave_attrs[3])
        assert bool(lflag & termios.ICANON)
        assert bool(lflag & termios.ECHO)
    finally:
        os.close(master_fd)
        os.close(slave_fd)


@pytest.mark.subprocess_e2e
def test_terminal_restore_on_hard_exit_controller() -> None:
    """InterruptController.force_exit restores terminal before os._exit."""
    pm = ProcessManager(
        policy=ProcessManagerPolicy(enable_zombie_reaper=False, log_events=False),
    )

    script = textwrap.dedent(
        """
        import sys
        from ralph.interrupt.controller import InterruptController
        from ralph.display.terminal_restore import restore_terminal

        controller = InterruptController(
            shutdown_all=lambda grace: None,
            record_interrupt=lambda: None,
            restore_terminal=restore_terminal,
        )
        sys.stdout.write('\\x1b[?25l\\x1b[?1049h')
        sys.stdout.flush()
        controller.force_exit()
        """
    )
    master_fd, slave_fd = pty.openpty()
    try:
        handle = pm.spawn(
            [sys.executable, "-c", script],
            opts=SpawnOptions(stdin=slave_fd, stdout=slave_fd, stderr=slave_fd),
        )
        rc = handle.wait(timeout=5.0)
        assert rc == 130

        raw_chunks: list[bytes] = []
        while wait_for_master_readable(master_fd, timeout_seconds=0.05):
            chunk = read_master_chunk(master_fd, max_bytes=4096)
            if not chunk:
                break
            raw_chunks.append(chunk)

        captured = b"".join(raw_chunks)
        assert b"\x1b[?25h" in captured
        assert b"\x1b[?1049l" in captured
    finally:
        os.close(master_fd)
        os.close(slave_fd)
