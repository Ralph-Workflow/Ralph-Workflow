"""Unit tests for Linux PTY fixes: EAGAIN handling and sentinel fd guard."""

from __future__ import annotations

import fcntl
import os

import pytest

from ralph.process._pty_process import PtyProcess
from ralph.process.pty import read_master_chunk


def test_read_master_chunk_raises_blocking_io_error_on_eagain() -> None:
    read_fd, write_fd = os.pipe()
    try:
        fcntl.fcntl(read_fd, fcntl.F_SETFL, os.O_NONBLOCK)
        with pytest.raises(BlockingIOError):
            read_master_chunk(read_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_pty_process_close_with_sentinel_slave_fd() -> None:
    proc = PtyProcess(pid=1, master_fd=-1, slave_fd=-1)
    proc.close()
    proc.close()
