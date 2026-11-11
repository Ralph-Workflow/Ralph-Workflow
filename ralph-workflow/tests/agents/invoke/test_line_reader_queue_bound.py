from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke._bounded_lines_queue import BoundedLinesQueue
from ralph.agents.invoke._process_reader import ProcessLineReader
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.invoke._types import ProcessReaderCtx
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig


def test_bounded_lines_queue_drops_oldest_when_full() -> None:
    q: BoundedLinesQueue = BoundedLinesQueue(maxlen=3)
    assert q.maxlen == 3
    q.append("a")
    q.append("b")
    q.append("c")
    assert q.snapshot() == ["a", "b", "c"]
    q.append("d")
    assert q.snapshot() == ["b", "c", "d"]
    q.append("e")
    assert q.snapshot() == ["c", "d", "e"]
    assert q.popleft() == "c"
    assert q.snapshot() == ["d", "e"]


def test_bounded_lines_queue_extend_drops_oldest() -> None:
    q: BoundedLinesQueue = BoundedLinesQueue(maxlen=3)
    q.extend(["a", "b", "c"])
    q.extend(["d", "e", "f"])
    assert q.snapshot() == ["d", "e", "f"]


def test_bounded_lines_queue_clear_empties() -> None:
    q: BoundedLinesQueue = BoundedLinesQueue(maxlen=2)
    q.append("a")
    q.append("b")
    q.clear()
    assert len(q) == 0
    assert not bool(q)


def test_bounded_lines_queue_rejects_non_positive_maxlen() -> None:
    with pytest.raises(ValueError, match="maxlen must be positive"):
        BoundedLinesQueue(maxlen=0)
    with pytest.raises(ValueError, match="maxlen must be positive"):
        BoundedLinesQueue(maxlen=-1)


def test_pre_parse_queue_cap_matches_parsed_output_tail() -> None:
    cap = 256
    q: BoundedLinesQueue = BoundedLinesQueue(maxlen=cap)
    assert q.maxlen == cap
    for index in range(cap * 3):
        q.append(f"line-{index}")
    assert len(q) == cap
    snapshot = q.snapshot()
    assert snapshot[0] == f"line-{cap * 2}"
    assert snapshot[-1] == f"line-{cap * 3 - 1}"


_PROCESS_LINE_READER_CAP = 256


class _FakeManagedProcess:
    stdin = None

    def __init__(self) -> None:
        self.pid: int | None = None
        self.stdout = iter([])

    def poll(self) -> int | None:
        return None

    def terminate(self, *, grace_period_s: float = 0.5) -> None:
        pass


def _make_subprocess_ctx(*, workspace_path: Path | None = None) -> ProcessReaderCtx:
    return ProcessReaderCtx(
        config=AgentConfig(cmd="test-agent", transport=AgentTransport.GENERIC),
        policy=TimeoutPolicy(idle_timeout_seconds=300.0),
        execution_strategy=None,
        liveness_probe=None,
        waiting_listener=None,
        monitor=None,
        workspace_path=workspace_path,
    )


def test_process_line_reader_installs_bounded_pre_parse_queue() -> None:
    handle = _FakeManagedProcess()
    ctx = _make_subprocess_ctx()
    reader = ProcessLineReader(handle, ctx, FakeClock(start=0.0))

    assert isinstance(reader._lines_queue, BoundedLinesQueue)
    assert reader._lines_queue.maxlen == _PROCESS_LINE_READER_CAP


def test_process_line_reader_queue_bounds_burst_output() -> None:
    handle = _FakeManagedProcess()
    ctx = _make_subprocess_ctx()
    reader = ProcessLineReader(handle, ctx, FakeClock(start=0.0))

    cap = _PROCESS_LINE_READER_CAP
    for index in range(cap * 3):
        reader._lines_queue.append(f"line-{index}")

    assert len(reader._lines_queue) == cap, (
        f"pre-parse queue MUST stay at cap under burst,"
        f" got len={len(reader._lines_queue)}, cap={cap}"
    )
    snapshot = reader._lines_queue.snapshot()
    assert snapshot[0] == f"line-{cap * 2}"
    assert snapshot[-1] == f"line-{cap * 3 - 1}"


class _FakePtyHandle:
    def __init__(self, master_fd: int) -> None:
        self.master_fd = master_fd
        self.pid: int | None = None

    def poll(self) -> int | None:
        return None

    def terminate(self, *, grace_period_s: float = 0.5) -> None:
        pass

    def descendant_snapshot(self) -> tuple[int, float | None]:
        return (0, None)


def _make_pty_ctx(*, workspace_path: object = None) -> SimpleNamespace:
    return SimpleNamespace(
        config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
        policy=TimeoutPolicy(idle_timeout_seconds=300.0),
        monitor=None,
        execution_strategy=None,
        liveness_probe=None,
        waiting_listener=None,
        workspace_path=workspace_path,
    )


def test_pty_line_reader_installs_bounded_pre_parse_queue() -> None:
    master_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        handle = _FakePtyHandle(master_fd)
        reader = PtyLineReader(
            handle,
            "test-agent",
            _make_pty_ctx(),
            FakeClock(start=0.0),
            extras=None,
        )
        assert isinstance(reader._lines_queue, BoundedLinesQueue)
        assert reader._lines_queue.maxlen == 256
    finally:
        os.close(master_fd)


def test_pty_line_reader_queue_bounds_burst_output() -> None:
    master_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        handle = _FakePtyHandle(master_fd)
        reader = PtyLineReader(
            handle,
            "test-agent",
            _make_pty_ctx(),
            FakeClock(start=0.0),
            extras=None,
        )
        cap = 256
        for index in range(cap * 3):
            reader._lines_queue.append(f"line-{index}")
        assert len(reader._lines_queue) == cap, (
            f"PTY pre-parse queue MUST stay at cap under burst,"
            f" got len={len(reader._lines_queue)}, cap={cap}"
        )
        snapshot = reader._lines_queue.snapshot()
        assert snapshot[0] == f"line-{cap * 2}"
        assert snapshot[-1] == f"line-{cap * 3 - 1}"
    finally:
        os.close(master_fd)
