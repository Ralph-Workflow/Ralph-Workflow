"""Regression coverage for PTY drain ownership during concurrent teardown."""

from __future__ import annotations

import os
from types import SimpleNamespace

from loguru import logger

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig


class _ExitedPipeHandle:
    def __init__(self, master_fd: int) -> None:
        self.master_fd = master_fd

    def poll(self) -> int:
        return 0


def _make_reader(master_fd: int) -> PtyLineReader:
    ctx = SimpleNamespace(
        config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
        policy=TimeoutPolicy(idle_timeout_seconds=300.0),
        monitor=None,
        execution_strategy=None,
        liveness_probe=None,
        waiting_listener=None,
    )
    return PtyLineReader(_ExitedPipeHandle(master_fd), "claude", ctx, FakeClock(), extras=None)


def _close_reader_fds(reader: PtyLineReader) -> None:
    os.close(reader._input_writer_fd)
    os.close(reader._read_fd)


def test_pty_drain_regression_keeps_buffered_output_after_handle_fd_closes() -> None:
    """S-1: closing the shared handle fd must not discard its final buffered output."""
    read_fd, write_fd = os.pipe()
    reader = _make_reader(read_fd)
    try:
        os.write(write_fd, b"agent final line\n")
        os.close(read_fd)
        read_fd = -1

        reader._read_thread()

        assert list(reader._lines_queue) == ["agent final line\n"]
    finally:
        if read_fd >= 0:
            os.close(read_fd)
        os.close(write_fd)
        _close_reader_fds(reader)


def test_pty_drain_regression_handles_reader_fd_closed_during_interrupt_teardown() -> None:
    """S-1: reader-private fd teardown must complete without an EBADF thread warning."""
    read_fd, write_fd = os.pipe()
    reader = _make_reader(read_fd)
    records: list[object] = []
    sink_id = logger.add(records.append, level="WARNING")
    try:
        os.close(reader._read_fd)
        reader._read_thread()

        assert reader._reader_done[0] is True
        assert not any(
            "PTY read thread" in str(record) or "Bad file descriptor" in str(record)
            for record in records
        )
    finally:
        logger.remove(sink_id)
        os.close(read_fd)
        os.close(write_fd)
        os.close(reader._input_writer_fd)
