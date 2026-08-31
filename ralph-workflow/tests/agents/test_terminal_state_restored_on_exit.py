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
import time
from pathlib import Path

import pytest

from ralph.process.manager import SpawnOptions
from ralph.process.manager._process_manager import ProcessManager
from ralph.process.manager._process_manager_policy import ProcessManagerPolicy
from ralph.process.pty import read_master_chunk, wait_for_master_readable

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]

# Every test here forks a fresh CPython that imports ``ralph.cli.main``
# before it can restore anything, so the interpreter start-up and import
# cost -- not the behaviour under test -- dominates the wall clock. That
# cost was measured at 0.6-1.05 s per test on an idle 12-core host, i.e.
# already at or over the 1.0 s default per-test SIGALRM deadline in
# ``tests/conftest.py``; under host load these tests failed non-
# deterministically, a different subset each run. The ceilings below are
# failsafes against a genuine hang, NOT waits: every one of them is
# reached only by a test that is already broken, because each wait below
# returns the instant its condition holds. ``subprocess_e2e`` keeps this
# file out of ``make test``, so none of this time is charged to the
# immutable 60 s combined budget.
_TEST_CEILING_SECONDS = 30.0
_CHILD_EXIT_CEILING_SECONDS = 15.0
_DRAIN_CEILING_SECONDS = 5.0
_DRAIN_POLL_SECONDS = 0.05
_READ_CHUNK_BYTES = 4096

pytestmark = pytest.mark.timeout_seconds(_TEST_CEILING_SECONDS)


def _subprocess_env() -> dict[str, str]:
    """Run child Python from the same locked package tree as this test."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_PACKAGE_ROOT)
    return env


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
            opts=SpawnOptions(stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, env=_subprocess_env()),
        )
        handle.wait(timeout=_CHILD_EXIT_CEILING_SECONDS)

        captured = _drain_pty(master_fd, expected=(b"\x1b[?25h", b"\x1b[?1049l"))
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
            opts=SpawnOptions(stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, env=_subprocess_env()),
        )
        assert handle.wait(timeout=_CHILD_EXIT_CEILING_SECONDS) == -signal.SIGTERM
        restored = (b"\x1b[?25h", b"\x1b[?1049l", b"\x1b[?1004l", b"\x1b[?1l", b"\x1b[r")
        captured = _drain_pty(master_fd, expected=restored)
        for sequence in restored:
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
            opts=SpawnOptions(stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, env=_subprocess_env()),
        )
        assert handle.wait(timeout=_CHILD_EXIT_CEILING_SECONDS) != 0
        restored = (b"\x1b[?25h", b"\x1b[?1049l", b"\x1b[?1000l", b"\x1b[?1006l")
        captured = _drain_pty(master_fd, expected=restored)
        for sequence in restored:
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
            opts=SpawnOptions(stdin=slave_fd, stdout=write_fd, stderr=slave_fd, env=_subprocess_env()),
        )
        os.close(write_fd)
        write_fd = -1
        assert handle.wait(timeout=_CHILD_EXIT_CEILING_SECONDS) == 0
        assert b"\x1b[?25h" in _drain_pty(master_fd, expected=(b"\x1b[?25h",))
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
            opts=SpawnOptions(stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, env=_subprocess_env()),
        )
        assert handle.wait(timeout=_CHILD_EXIT_CEILING_SECONDS) == 0
        lflag = int(termios.tcgetattr(slave_fd)[3])
        assert lflag & termios.ICANON
        assert lflag & termios.ECHO
    finally:
        os.close(master_fd)
        os.close(slave_fd)


def _drain_pty(master_fd: int, *, expected: tuple[bytes, ...]) -> bytes:
    """Read the PTY master until every ``expected`` sequence has arrived.

    The exit condition is the arrival of the bytes the caller is about to
    assert on, not the expiry of a fixed read window: a poll that comes
    back empty is only decisive once ``expected`` is fully present. A
    drain that stopped after one quiet 10 ms window instead assumed the
    child's writes had already reached the line discipline, which is a
    scheduling assumption and not a fact -- it is what made these tests
    fail under host load. Draining continues past the last ``expected``
    match until the master goes quiet, so callers may still assert that
    a sequence is ABSENT from the full capture.

    Args:
        master_fd: PTY master file descriptor to read.
        expected: Byte sequences whose presence ends the drain. Pass the
            sequences the test asserts must appear.

    Returns:
        Everything read from the master, which is all of the child's
        output once the child has exited.
    """
    chunks: list[bytes] = []
    deadline = time.monotonic() + _DRAIN_CEILING_SECONDS
    while True:
        if wait_for_master_readable(master_fd, timeout_seconds=_DRAIN_POLL_SECONDS):
            chunk = read_master_chunk(master_fd, max_bytes=_READ_CHUNK_BYTES)
            if chunk:
                chunks.append(chunk)
                continue
        captured = b"".join(chunks)
        if all(sequence in captured for sequence in expected):
            return captured
        if time.monotonic() >= deadline:
            return captured


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
            opts=SpawnOptions(stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, env=_subprocess_env()),
        )
        rc = handle.wait(timeout=_CHILD_EXIT_CEILING_SECONDS)
        assert rc == 130

        captured = _drain_pty(master_fd, expected=(b"\x1b[?25h", b"\x1b[?1049l"))
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
            opts=SpawnOptions(stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, env=_subprocess_env())
        )
        assert handle.wait(timeout=_CHILD_EXIT_CEILING_SECONDS) != 0
        captured = _drain_pty(master_fd, expected=(b"\x1b[?25h", b"\x1b[?1049l"))
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
