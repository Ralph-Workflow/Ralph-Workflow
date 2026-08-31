"""Real-PTY regression tests for ownership-safe terminal restoration."""

from __future__ import annotations

import os
import pty
import select
import signal
import sys
import termios
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from ralph.process.manager import SpawnOptions
from ralph.process.manager._process_manager import ProcessManager
from ralph.process.manager._process_manager_policy import ProcessManagerPolicy
from ralph.process.pty import read_master_chunk, wait_for_master_readable

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_TEST_CEILING_SECONDS = 30.0
_CHILD_EXIT_CEILING_SECONDS = 15.0
_DRAIN_CEILING_SECONDS = 5.0
_DRAIN_POLL_SECONDS = 0.05
_READ_CHUNK_BYTES = 4096
_READY = b"child-ready"
_MODES_FRAME_PREFIX = b"RALPH-TERMIOS-V1:"
_MODES_FRAME_SUFFIX = b":END"
_TRIGGER = b"!"
_QUEUED_INPUT = b"queued-before-exit\n"
_CURSOR_SHOW = b"\x1b[?25h"
_FORBIDDEN_RESTORE_BYTES = (
    b"\x1b[?1049l",
    b"\x1b[?1047l",
    b"\x1b[?47l",
    b"\x1b[J",
    b"\x1b[0J",
    b"\x1b[1J",
    b"\x1b[2J",
    b"\x1b[3J",
)
_SNAPSHOT_REPORT = f"""\
snapshot = termios.tcgetattr(0)
snapshot_payload = (
    b",".join(str(value).encode("ascii") for value in snapshot[:6])
    + b"|"
    + b",".join(
        b"i" + str(value).encode("ascii")
        if isinstance(value, int)
        else b"b" + value.hex().encode("ascii")
        for value in snapshot[6]
    )
)
sys.stderr.buffer.write(
    {_MODES_FRAME_PREFIX!r}
    + str(len(snapshot_payload)).encode("ascii")
    + b":"
    + snapshot_payload
    + {_MODES_FRAME_SUFFIX!r}
    + {_READY!r}
)
sys.stderr.buffer.flush()
"""

pytestmark = pytest.mark.timeout_seconds(_TEST_CEILING_SECONDS)


@dataclass(frozen=True, slots=True)
class _ExitObservation:
    return_code: int
    output: bytes
    modes_before: list[int | list[bytes | int]]
    modes_after: list[int | list[bytes | int]]
    queued_input: bytes
    redirected_stdout: bytes


def _subprocess_env() -> dict[str, str]:
    """Run child Python from the same locked package tree as this test."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_PACKAGE_ROOT)
    env["TERM"] = "xterm-256color"
    return env


def _observe_exit(script: str, *, redirect_stdout: bool = False) -> _ExitObservation:
    """Run one controlled exit and observe its PTY stream and kernel state."""
    pm = ProcessManager(
        policy=ProcessManagerPolicy(enable_zombie_reaper=False, log_events=False),
    )
    master_fd, slave_fd = pty.openpty()
    fixture_modes = termios.tcgetattr(slave_fd)
    if hasattr(termios, "PENDIN"):
        fixture_modes[3] |= termios.PENDIN
        termios.tcsetattr(slave_fd, termios.TCSANOW, fixture_modes)
    read_fd = -1
    write_fd = -1
    try:
        if redirect_stdout:
            read_fd, write_fd = os.pipe()
        handle = pm.spawn(
            [sys.executable, "-c", script],
            opts=SpawnOptions(
                stdin=slave_fd,
                stdout=write_fd if redirect_stdout else slave_fd,
                stderr=slave_fd,
                env=_subprocess_env(),
            ),
        )
        if write_fd >= 0:
            os.close(write_fd)
            write_fd = -1
        output = _read_until(master_fd, expected=_READY)
        modes_before = _parse_modes_snapshot(output)
        os.write(master_fd, _TRIGGER + _QUEUED_INPUT)
        return_code = handle.wait(timeout=_CHILD_EXIT_CEILING_SECONDS)
        output += _drain_pty(master_fd, expected=(_CURSOR_SHOW,))
        modes_after = termios.tcgetattr(slave_fd)
        queued_input = _read_queued_input(slave_fd)
        redirected = os.read(read_fd, _READ_CHUNK_BYTES) if read_fd >= 0 else b""
        return _ExitObservation(
            return_code=return_code,
            output=output,
            modes_before=modes_before,
            modes_after=modes_after,
            queued_input=queued_input,
            redirected_stdout=redirected,
        )
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        if read_fd >= 0:
            os.close(read_fd)
        os.close(master_fd)
        os.close(slave_fd)


def _read_until(master_fd: int, *, expected: bytes) -> bytes:
    """Read PTY output until a child synchronization marker arrives."""
    chunks: list[bytes] = []
    deadline = time.monotonic() + _DRAIN_CEILING_SECONDS
    while expected not in b"".join(chunks) and time.monotonic() < deadline:
        if wait_for_master_readable(master_fd, timeout_seconds=_DRAIN_POLL_SECONDS):
            chunk = read_master_chunk(master_fd, max_bytes=_READ_CHUNK_BYTES)
            if chunk:
                chunks.append(chunk)
    captured = b"".join(chunks)
    assert expected in captured, f"child synchronization marker missing from {captured!r}"
    return captured


def _parse_modes_snapshot(output: bytes) -> list[int | list[bytes | int]]:
    """Parse the length-framed child-owned termios snapshot."""
    frame_start = output.index(_MODES_FRAME_PREFIX) + len(_MODES_FRAME_PREFIX)
    length_end = output.index(b":", frame_start)
    payload_length = int(output[frame_start:length_end])
    payload_start = length_end + 1
    payload_end = payload_start + payload_length
    assert output[payload_end : payload_end + len(_MODES_FRAME_SUFFIX)] == _MODES_FRAME_SUFFIX
    flags_payload, controls_payload = output[payload_start:payload_end].split(b"|", 1)
    flags = [int(value) for value in flags_payload.split(b",")]
    assert len(flags) == 6
    controls: list[bytes | int] = []
    for value in controls_payload.split(b","):
        if value.startswith(b"i"):
            controls.append(int(value[1:]))
        else:
            assert value.startswith(b"b")
            controls.append(bytes.fromhex(value[1:].decode("ascii")))
    return [*flags, controls]


def _drain_pty(master_fd: int, *, expected: tuple[bytes, ...]) -> bytes:
    """Drain exited-child output until all expected restore bytes arrive."""
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


def _read_queued_input(slave_fd: int) -> bytes:
    """Read queued operator input without risking a blocking test."""
    readable, _, _ = select.select([slave_fd], [], [], 0.0)
    assert readable, "queued input was discarded before the process exited"
    return os.read(slave_fd, _READ_CHUNK_BYTES)


def _assert_ownership_safe_restore(observation: _ExitObservation) -> None:
    """Assert the shared byte-stream and kernel restoration contract."""
    assert _CURSOR_SHOW in observation.output
    for sequence in _FORBIDDEN_RESTORE_BYTES:
        assert sequence not in observation.output
    assert observation.modes_after == observation.modes_before
    assert observation.queued_input == _QUEUED_INPUT


@pytest.mark.subprocess_e2e
def test_terminal_restore_regression_normal_exit_preserves_parent_state() -> None:
    """S-4: normal exit restores owned state without destructive controls."""
    script = textwrap.dedent(
        f"""
        import os
        import sys
        import termios
        from ralph.cli.main import ensure_cli_terminal_restore

        ensure_cli_terminal_restore()
        {_SNAPSHOT_REPORT.replace(chr(10), chr(10) + "        ")}
        os.read(0, 1)
        """
    )

    observation = _observe_exit(script)

    assert observation.return_code == 0
    _assert_ownership_safe_restore(observation)


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT], ids=["sigterm", "sigint"])
@pytest.mark.subprocess_e2e
def test_terminal_restore_regression_signal_exit_preserves_parent_state(signum: int) -> None:
    """S-4: SIGTERM and SIGINT restore the exact pre-run line discipline."""
    script = textwrap.dedent(
        f"""
        import os
        import signal
        import sys
        import termios
        import tty
        from ralph.cli.main import ensure_cli_terminal_restore

        ensure_cli_terminal_restore()
        {_SNAPSHOT_REPORT.replace(chr(10), chr(10) + "        ")}
        tty.setraw(0)
        os.read(0, 1)
        os.kill(os.getpid(), {signum})
        """
    )

    observation = _observe_exit(script)

    assert observation.return_code != 0
    _assert_ownership_safe_restore(observation)


@pytest.mark.subprocess_e2e
def test_terminal_restore_regression_redirected_stdout_uses_terminal_stderr() -> None:
    """S-4: redirected stdout leaves restore bytes on the terminal only."""
    script = textwrap.dedent(
        f"""
        import os
        import sys
        import termios
        from ralph.cli.main import ensure_cli_terminal_restore

        ensure_cli_terminal_restore()
        {_SNAPSHOT_REPORT.replace(chr(10), chr(10) + "        ")}
        os.read(0, 1)
        """
    )

    observation = _observe_exit(script, redirect_stdout=True)

    assert observation.return_code == 0
    assert observation.redirected_stdout == b""
    _assert_ownership_safe_restore(observation)


@pytest.mark.subprocess_e2e
def test_terminal_restore_regression_normal_exit_restores_raw_termios() -> None:
    """S-4: raw mode is replaced by the exact pre-run line discipline."""
    script = textwrap.dedent(
        f"""
        import os
        import sys
        import termios
        import tty
        from ralph.cli.main import ensure_cli_terminal_restore

        ensure_cli_terminal_restore()
        {_SNAPSHOT_REPORT.replace(chr(10), chr(10) + "        ")}
        tty.setraw(0)
        os.read(0, 1)
        """
    )

    observation = _observe_exit(script)

    assert observation.return_code == 0
    _assert_ownership_safe_restore(observation)


@pytest.mark.subprocess_e2e
def test_terminal_restore_regression_force_exit_preserves_parent_state() -> None:
    """S-4: InterruptController.force_exit restores before os._exit."""
    script = textwrap.dedent(
        f"""
        import os
        import sys
        import termios
        import tty
        from ralph.cli.main import ensure_cli_terminal_restore
        from ralph.interrupt.controller import InterruptController

        ensure_cli_terminal_restore()
        {_SNAPSHOT_REPORT.replace(chr(10), chr(10) + "        ")}
        tty.setraw(0)
        controller = InterruptController(
            shutdown_all=lambda grace: None,
            record_interrupt=lambda: None,
        )
        os.read(0, 1)
        controller.force_exit()
        """
    )

    observation = _observe_exit(script)

    assert observation.return_code == 130
    _assert_ownership_safe_restore(observation)


@pytest.mark.subprocess_e2e
def test_terminal_restore_regression_crash_is_sanitized_and_preserves_parent_state() -> None:
    """S-4: uncaught crash text is sanitized before terminal restoration."""
    script = textwrap.dedent(
        f"""
        import os
        import sys
        import termios
        import tty
        from ralph.cli.main import ensure_cli_terminal_restore

        ensure_cli_terminal_restore()
        {_SNAPSHOT_REPORT.replace(chr(10), chr(10) + "        ")}
        tty.setraw(0)
        os.read(0, 1)
        raise RuntimeError("agent: \\x1b[?1003h\\x1b[?1006h\\x1b[2J")
        """
    )

    observation = _observe_exit(script)

    assert observation.return_code != 0
    assert b"RuntimeError: agent:" in observation.output
    assert b"\x1b[?1003h" not in observation.output
    assert b"\x1b[?1006h" not in observation.output
    _assert_ownership_safe_restore(observation)


@pytest.mark.subprocess_e2e
def test_queued_input_assertion_regression_rejects_old_tciflush() -> None:
    """S-4: the queue assertion detects the old generic TCIFLUSH behavior."""
    master_fd, slave_fd = pty.openpty()
    try:
        os.write(master_fd, _QUEUED_INPUT)
        readable, _, _ = select.select([slave_fd], [], [], 0.0)
        assert readable
        termios.tcflush(slave_fd, termios.TCIFLUSH)
        with pytest.raises(AssertionError, match="queued input was discarded"):
            _read_queued_input(slave_fd)
    finally:
        os.close(master_fd)
        os.close(slave_fd)
