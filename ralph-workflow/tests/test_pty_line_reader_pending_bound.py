from __future__ import annotations

import os
import threading

from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.timeout_clock import SystemClock
from tests.agents.invoke.test_line_reader_queue_bound import _FakePtyHandle, _make_pty_ctx


def _write_all(fd: int, payload: bytes) -> None:
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            view = view[written:]
    finally:
        os.close(fd)


def test_pty_reader_regression_overlong_pending_tail_yields_newest_complete_line() -> None:
    """S-5 / DA-002: the public reader retains the newest completed PTY line."""
    read_fd, write_fd = os.pipe()
    reader = PtyLineReader(
        _FakePtyHandle(read_fd),
        "test-agent",
        _make_pty_ctx(),
        SystemClock(),
        extras=None,
        max_pending_chars=4096,
    )
    producer = threading.Thread(
        target=_write_all,
        args=(write_fd, b"x" * 4097 + b"complete line\n"),
        daemon=True,
    )
    producer.start()
    try:
        lines = list(reader.read_lines())
        producer.join(timeout=0.5)

        assert not producer.is_alive()
        assert lines == ["x" * (4096 - len("complete line\n")) + "complete line\n"]
    finally:
        producer.join(timeout=0.5)
        os.close(read_fd)
