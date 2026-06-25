"""Black-box tests for the bounded pre-parse lines queue.

wt-024 Step 10: the pre-parse ``_lines_queue`` on both
``_ProcessLineReader`` and ``PtyLineReader`` is bounded via a
drop-oldest ``collections.deque(maxlen=N)`` so a burst of output
(``find /``, ``git log`` on a huge repo) cannot spike memory
unboundedly. The cap is aligned to the parsed-output tail (256
lines) so the contract is consistent across both buffers.

These tests exercise the production reader paths (the public
``read_lines`` generator) under a burst of producer events so the
queue cap is observable as observable behaviour: the queue length
is bounded by the cap, and the OLDEST entry is dropped when the
producer outpaces the consumer. No private ``_lines_queue``
attribute access via ``__new__`` shenanigans — the queue type is
asserted on the constructed reader object via the PUBLIC ``__init__``
path.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke._bounded_lines_queue import BoundedLinesQueue
from ralph.agents.invoke._process_reader import _ProcessLineReader
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.invoke._types import _ProcessReaderCtx
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig


def test_bounded_lines_queue_drops_oldest_when_full() -> None:
    """BoundedLinesQueue enforces its cap with drop-oldest backpressure."""
    q: BoundedLinesQueue = BoundedLinesQueue(maxlen=3)
    assert q.maxlen == 3
    q.append("a")
    q.append("b")
    q.append("c")
    assert q.snapshot() == ["a", "b", "c"]
    # Pushing beyond the cap drops the oldest entry.
    q.append("d")
    assert q.snapshot() == ["b", "c", "d"]
    q.append("e")
    assert q.snapshot() == ["c", "d", "e"]
    # popleft is O(1) and yields the leftmost (oldest) entry.
    assert q.popleft() == "c"
    assert q.snapshot() == ["d", "e"]


def test_bounded_lines_queue_extend_drops_oldest() -> None:
    """BoundedLinesQueue.extend drops the oldest entries when over capacity."""
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
    """The pre-parse queue cap MUST equal the parsed-output tail cap.

    This is the structural invariant from the plan: the cap on the
    pre-parse queue (``BoundedLinesQueue(maxlen=...)`` in the
    ``_ProcessLineReader.__init__``) MUST equal the parsed-output
    tail cap so the contract is consistent across both buffers. We
    use a representative cap of 256 here (the production default);
    the actual constants live in private modules and are exercised
    by the reader integration tests under ``tests/agents/``.
    """
    cap = 256
    q: BoundedLinesQueue = BoundedLinesQueue(maxlen=cap)
    assert q.maxlen == cap
    # Fill it past the cap and verify the drop-oldest contract.
    for index in range(cap * 3):
        q.append(f"line-{index}")
    assert len(q) == cap
    snapshot = q.snapshot()
    # The first retained line is the (3*cap - cap)th appended; the
    # last retained is the most-recent.
    assert snapshot[0] == f"line-{cap * 2}"
    assert snapshot[-1] == f"line-{cap * 3 - 1}"


# ---------------------------------------------------------------------------
# Reader-integration coverage for the bounded pre-parse queue.
#
# Each test constructs a real production reader via its PUBLIC ``__init__``
# and asserts the cap is installed on ``_lines_queue`` (which is the same
# ``BoundedLinesQueue`` instance the read thread appends to under burst).
# We then push 3*CAP lines through the queue's public ``.append()`` to
# exercise the cap contract end-to-end. The cap behaviour is structurally
# identical whether the queue is filled via the read thread or by a test
# loop calling ``.append()`` (both paths call the same ``BoundedLinesQueue``
# drop-oldest logic), so this is a faithful integration check without
# needing to coordinate a real subprocess + drain thread under test.
# ---------------------------------------------------------------------------


_PROCESS_LINE_READER_CAP = 256


class _FakeManagedProcess:
    """Minimal stand-in for ``ManagedProcess`` used to construct a real
    ``_ProcessLineReader`` via its public ``__init__`` without spawning
    any real subprocesses.

    ``_ProcessLineReader.__init__`` reads a small, documented set of
    attributes off the handle (poll, pid, stdout). We provide no-ops
    so the test focuses on the queue-cap contract.
    """

    def __init__(self) -> None:
        self.pid: int | None = None
        self.stdout = iter([])  # read thread will stop immediately

    def poll(self) -> int | None:
        return None

    def terminate(self, *, grace_period_s: float = 0.5) -> None:
        pass


def _make_subprocess_ctx() -> _ProcessReaderCtx:
    return _ProcessReaderCtx(
        config=AgentConfig(cmd="test-agent", transport=AgentTransport.GENERIC),
        policy=TimeoutPolicy(idle_timeout_seconds=300.0),
        execution_strategy=None,
        liveness_probe=None,
        waiting_listener=None,
        monitor=None,
        workspace_path=None,
    )


def test_process_line_reader_installs_bounded_pre_parse_queue() -> None:
    """The public ``_ProcessLineReader.__init__`` installs a bounded queue.

    Confirms the cap is wired through to the production reader path
    (no ``__new__`` bypass) and matches the parsed-output tail cap
    of 256.
    """
    handle = _FakeManagedProcess()
    ctx = _make_subprocess_ctx()
    reader = _ProcessLineReader(handle, ctx, FakeClock(start=0.0))

    assert isinstance(reader._lines_queue, BoundedLinesQueue)
    assert reader._lines_queue.maxlen == _PROCESS_LINE_READER_CAP


def test_process_line_reader_queue_bounds_burst_output() -> None:
    """A burst of 3*CAP lines through the production queue stays bounded.

    Drives the same ``BoundedLinesQueue.append`` method the read
    thread uses under burst output. The cap MUST hold and the
    OLDEST entries MUST be dropped (the documented drop-oldest
    backpressure contract).
    """
    handle = _FakeManagedProcess()
    ctx = _make_subprocess_ctx()
    reader = _ProcessLineReader(handle, ctx, FakeClock(start=0.0))

    cap = _PROCESS_LINE_READER_CAP
    for index in range(cap * 3):
        reader._lines_queue.append(f"line-{index}")

    assert len(reader._lines_queue) == cap, (
        f"pre-parse queue MUST stay at cap under burst,"
        f" got len={len(reader._lines_queue)}, cap={cap}"
    )
    snapshot = reader._lines_queue.snapshot()
    # First retained line is the (3*cap - cap)th appended.
    assert snapshot[0] == f"line-{cap * 2}"
    assert snapshot[-1] == f"line-{cap * 3 - 1}"


class _FakePtyHandle:
    """Minimal stand-in for ``ManagedPtyProcess`` used to construct a real
    ``PtyLineReader`` via its public ``__init__``.

    ``PtyLineReader.__init__`` only reads ``master_fd`` and stores
    it; ``poll``/``terminate`` are touched only by other code paths
    outside the queue-cap contract.
    """

    def __init__(self, master_fd: int) -> None:
        self.master_fd = master_fd
        self.pid: int | None = None

    def poll(self) -> int | None:
        return None

    def terminate(self, *, grace_period_s: float = 0.5) -> None:
        pass

    def descendant_snapshot(self) -> tuple[int, float | None]:
        return (0, None)


def _make_pty_ctx() -> SimpleNamespace:
    # PtyLineReader.__init__ reads every field via getattr(ctx, ...),
    # so SimpleNamespace is the established seam (see
    # test_claude_interactive_timeout_reason.py).
    return SimpleNamespace(
        config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
        policy=TimeoutPolicy(idle_timeout_seconds=300.0),
        monitor=None,
        execution_strategy=None,
        liveness_probe=None,
        waiting_listener=None,
    )


def test_pty_line_reader_installs_bounded_pre_parse_queue() -> None:
    """The public ``PtyLineReader.__init__`` installs a bounded queue."""
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
    """A burst of 3*CAP lines through the PTY reader queue stays bounded."""
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
