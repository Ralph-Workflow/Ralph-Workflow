"""End-to-end terminal restoration tests under a real PTY.

Exercises normal exit and signal-driven hard exits in child processes
under openpty to verify cursor visibility, alternate screen, mouse tracking,
bracketed paste, and termios line discipline are fully restored.
"""

from __future__ import annotations

import os
import pty
import signal
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
def test_terminal_restore_regression_sigterm_resets_input_and_display_modes() -> None:
    """S-7: SIGTERM restores every mode a crashed TUI can leave behind."""
    pm = ProcessManager(
        policy=ProcessManagerPolicy(enable_zombie_reaper=False, log_events=False),
    )
    script = textwrap.dedent(
        """
        import os
        import signal
        import sys
        from ralph.cli.main import ensure_cli_terminal_restore

        ensure_cli_terminal_restore()
        sys.stdout.write('\\x1b[?25l\\x1b[?1049h\\x1b[?1004h\\x1b[?1h\\x1b[5;10r')
        sys.stdout.flush()
        os.kill(os.getpid(), signal.SIGTERM)
        """
    )
    master_fd, slave_fd = pty.openpty()
    try:
        handle = pm.spawn(
            [sys.executable, "-c", script],
            opts=SpawnOptions(stdin=slave_fd, stdout=slave_fd, stderr=slave_fd),
        )
        assert handle.wait(timeout=5.0) == -signal.SIGTERM
        captured = _drain_pty(master_fd)
        for sequence in (b"\x1b[?25h", b"\x1b[?1049l", b"\x1b[?1004l", b"\x1b[?1l", b"\x1b[r"):
            assert sequence in captured
    finally:
        os.close(master_fd)
        os.close(slave_fd)


@pytest.mark.subprocess_e2e
def test_terminal_restore_regression_sigint_resets_input_and_display_modes() -> None:
    """SIGINT preserves KeyboardInterrupt semantics while atexit restores the terminal."""
    pm = ProcessManager(
        policy=ProcessManagerPolicy(enable_zombie_reaper=False, log_events=False),
    )
    script = textwrap.dedent(
        """
        import os
        import signal
        import sys
        import tty
        from ralph.cli.main import ensure_cli_terminal_restore

        ensure_cli_terminal_restore()
        tty.setraw(0)
        sys.stdout.write('\\x1b[?25l\\x1b[?1049h\\x1b[?1000h\\x1b[?1006h')
        sys.stdout.flush()
        os.kill(os.getpid(), signal.SIGINT)
        """
    )
    master_fd, slave_fd = pty.openpty()
    try:
        handle = pm.spawn(
            [sys.executable, "-c", script],
            opts=SpawnOptions(stdin=slave_fd, stdout=slave_fd, stderr=slave_fd),
        )
        assert handle.wait(timeout=5.0) != 0
        captured = _drain_pty(master_fd)
        for sequence in (b"\x1b[?25h", b"\x1b[?1049l", b"\x1b[?1000l", b"\x1b[?1006l"):
            assert sequence in captured
        lflag = int(termios.tcgetattr(slave_fd)[3])
        assert lflag & termios.ICANON
        assert lflag & termios.ECHO
    finally:
        os.close(master_fd)
        os.close(slave_fd)


@pytest.mark.subprocess_e2e
def test_terminal_restore_regression_redirected_stdout_uses_terminal_stderr() -> None:
    """S-7: normal exit reaches the controlling terminal when stdout is redirected."""
    pm = ProcessManager(
        policy=ProcessManagerPolicy(enable_zombie_reaper=False, log_events=False),
    )
    read_fd, write_fd = os.pipe()
    master_fd, slave_fd = pty.openpty()
    script = "from ralph.cli.main import ensure_cli_terminal_restore; ensure_cli_terminal_restore()"
    try:
        handle = pm.spawn(
            [sys.executable, "-c", script],
            opts=SpawnOptions(stdin=slave_fd, stdout=write_fd, stderr=slave_fd),
        )
        os.close(write_fd)
        write_fd = -1
        assert handle.wait(timeout=5.0) == 0
        assert b"\x1b[?25h" in _drain_pty(master_fd)
        assert os.read(read_fd, 4096) == b""
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)
        os.close(master_fd)
        os.close(slave_fd)


@pytest.mark.subprocess_e2e
def test_terminal_restore_regression_normal_exit_restores_raw_termios() -> None:
    """S-7: the CLI snapshot survives probing and restores raw terminal modes."""
    pm = ProcessManager(
        policy=ProcessManagerPolicy(enable_zombie_reaper=False, log_events=False),
    )
    script = textwrap.dedent(
        """
        import tty
        from ralph.cli.main import ensure_cli_terminal_restore

        ensure_cli_terminal_restore()
        tty.setraw(0)
        """
    )
    master_fd, slave_fd = pty.openpty()
    try:
        handle = pm.spawn(
            [sys.executable, "-c", script],
            opts=SpawnOptions(stdin=slave_fd, stdout=slave_fd, stderr=slave_fd),
        )
        assert handle.wait(timeout=5.0) == 0
        lflag = int(termios.tcgetattr(slave_fd)[3])
        assert lflag & termios.ICANON
        assert lflag & termios.ECHO
    finally:
        os.close(master_fd)
        os.close(slave_fd)


def _drain_pty(master_fd: int) -> bytes:
    chunks: list[bytes] = []
    while wait_for_master_readable(master_fd, timeout_seconds=0.05):
        chunk = read_master_chunk(master_fd, max_bytes=4096)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


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


@pytest.mark.subprocess_e2e
def test_terminal_restore_regression_crash_output_is_sanitized_and_restored() -> None:
    """S-9: an unhandled crash cannot leak VT bytes or leave the PTY raw."""
    pm = ProcessManager(
        policy=ProcessManagerPolicy(enable_zombie_reaper=False, log_events=False),
    )
    script = textwrap.dedent(
        """
        import sys
        import tty
        from ralph.cli.main import ensure_cli_terminal_restore

        ensure_cli_terminal_restore()
        tty.setraw(0)
        raise RuntimeError("agent: \\x1b[?1003h\\x1b[?1006h\\x1b[?25l")
        """
    )
    master_fd, slave_fd = pty.openpty()
    try:
        handle = pm.spawn(
            [sys.executable, "-c", script],
            opts=SpawnOptions(stdin=slave_fd, stdout=slave_fd, stderr=slave_fd),
        )
        assert handle.wait(timeout=5.0) != 0
        captured = _drain_pty(master_fd)
        assert b"\x1b[?1003h" not in captured
        assert b"\x1b[?1006h" not in captured
        assert b"\x1b[?25l" not in captured
        assert b"\x1b[?25h" in captured
        assert b"\x1b[?1049l" in captured
        lflag = int(termios.tcgetattr(slave_fd)[3])
        assert lflag & termios.ICANON
        assert lflag & termios.ECHO
    finally:
        os.close(master_fd)
        os.close(slave_fd)
