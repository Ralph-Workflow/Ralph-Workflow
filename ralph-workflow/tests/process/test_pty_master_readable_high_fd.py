"""``wait_for_master_readable`` must work for descriptors numbered >= 1024.

``select.select`` cannot watch a descriptor at or above ``FD_SETSIZE`` (1024)
and raises ``ValueError`` instead. Hosts routinely raise ``RLIMIT_NOFILE`` far
past that (this one: 1048576), so a long run whose PTY master lands high made
the PTY read thread's ``except Exception`` treat a live agent as finished.
"""

from __future__ import annotations

import errno
import os
import resource
from typing import TYPE_CHECKING

import pytest

from ralph.process.pty import read_master_chunk, wait_for_master_readable

if TYPE_CHECKING:
    from collections.abc import Iterator

#: First descriptor number ``select.select`` refuses (FD_SETSIZE).
_FD_SETSIZE = 1024
_HIGH_FD = _FD_SETSIZE + 476


@pytest.fixture
def high_pty_master() -> Iterator[tuple[int, int]]:
    """Yield ``(master, slave)`` with the master duplicated onto a high fd number."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    raised = soft <= _HIGH_FD
    if raised:
        resource.setrlimit(resource.RLIMIT_NOFILE, (min(hard, _HIGH_FD + 64), hard))
    master_fd, slave_fd = os.openpty()
    high_fd = os.dup2(master_fd, _HIGH_FD)
    os.close(master_fd)
    try:
        yield high_fd, slave_fd
    finally:
        for fd in (high_fd, slave_fd):
            try:
                os.close(fd)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise
        if raised:
            resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def test_quiet_high_fd_master_is_not_readable(high_pty_master: tuple[int, int]) -> None:
    master_fd, _slave_fd = high_pty_master
    assert master_fd >= _FD_SETSIZE
    assert wait_for_master_readable(master_fd, 0.0) is False


def test_high_fd_master_with_output_is_readable(high_pty_master: tuple[int, int]) -> None:
    master_fd, slave_fd = high_pty_master
    os.write(slave_fd, b"agent output\n")
    assert wait_for_master_readable(master_fd, 1.0) is True
    assert b"agent output" in read_master_chunk(master_fd)


def test_high_fd_master_reports_hangup_as_readable(high_pty_master: tuple[int, int]) -> None:
    master_fd, slave_fd = high_pty_master
    os.close(slave_fd)
    assert wait_for_master_readable(master_fd, 1.0) is True
    assert read_master_chunk(master_fd) == b""


def test_closed_descriptor_still_raises_ebadf() -> None:
    """Callers treat EBADF as "the handle was closed"; that contract must hold."""
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    os.close(read_fd)
    with pytest.raises(OSError) as excinfo:
        wait_for_master_readable(read_fd, 0.0)
    assert excinfo.value.errno == errno.EBADF
