"""A line the bounded queue drops must still reach the raw capture.

The pre-parse queue is drop-oldest, and the raw capture is written on the
CONSUMER side -- so every line the queue dropped was a line the consumer
never saw and the transcript never recorded. Losing whole lines leaves
the file parseable, so ``detect_raw_log_breaks`` reported nothing and the
loss was silent: a 1000-line burst reached the consumer as 256 and the
missing 744 were never accounted for anywhere.

Both readers wire the queue's eviction sink to their capture in
``__init__``. These tests pin that wiring on the real reader objects,
built through their public constructors, because the queue-level test
passes just as happily when neither reader is connected to it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke._process_reader import ProcessLineReader
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.timeout_clock import FakeClock
from ralph.display.raw_overflow import (
    get_or_create_raw_overflow_log,
    raw_log_path_for,
    raw_log_unit_id_for,
    reset_raw_overflow_path_state,
)
from tests.agents.invoke.test_line_reader_queue_bound import (
    _FakeManagedProcess,
    _FakePtyHandle,
    _make_pty_ctx,
    _make_subprocess_ctx,
)

if TYPE_CHECKING:
    from ralph.config.models import AgentConfig

_CAP = 256
# Deliberately larger than the cap: a batch this size displaces lines
# that are not in the deque yet, which is the case the first version of
# the sink missed entirely.
_BURST = 1000


def _captured_lines(workspace: Path, reader_config: AgentConfig) -> list[str]:
    path = raw_log_path_for(workspace, raw_log_unit_id_for(reader_config))
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def test_a_process_reader_burst_loses_no_line(tmp_path: Path) -> None:
    """Every line of an over-cap burst is either queued or captured."""
    reset_raw_overflow_path_state()
    ctx = _make_subprocess_ctx(workspace_path=tmp_path)
    reader = ProcessLineReader(_FakeManagedProcess(), ctx, FakeClock(start=0.0))

    produced = [f"line-{index}" for index in range(_BURST)]
    reader._lines_queue.extend(produced)
    reader._raw_overflow.close()

    queued = reader._lines_queue.snapshot()
    captured = _captured_lines(tmp_path, ctx.config)

    assert len(queued) == _CAP
    assert set(produced) <= set(captured) | set(queued)
    assert captured == produced[: _BURST - _CAP]


def test_a_pty_reader_burst_loses_no_line(tmp_path: Path) -> None:
    """The same guarantee on the interactive reader."""
    reset_raw_overflow_path_state()
    master_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        ctx = _make_pty_ctx(workspace_path=tmp_path)
        reader = PtyLineReader(
            _FakePtyHandle(master_fd),
            "test-agent",
            ctx,
            FakeClock(start=0.0),
            extras=None,
        )
        # The PTY reader opens its capture when the read loop starts;
        # the sink must already be pointed at it before any line moves.
        reader._raw_overflow = get_or_create_raw_overflow_log(
            tmp_path,
            raw_log_unit_id_for(ctx.config),
            model=ctx.config.model,
        )

        produced = [f"line-{index}" for index in range(_BURST)]
        reader._lines_queue.extend(produced)
        reader._raw_overflow.close()

        queued = reader._lines_queue.snapshot()
        captured = _captured_lines(tmp_path, ctx.config)

        assert len(queued) == _CAP
        assert set(produced) <= set(captured) | set(queued)
        assert captured == produced[: _BURST - _CAP]
    finally:
        os.close(master_fd)


def test_an_eviction_write_does_not_claim_the_unit_is_progressing(tmp_path: Path) -> None:
    """Eviction writes must not advance the watchdog's liveness signal.

    ``size_bytes`` answers "is this unit still making progress", and
    every other append is consumer-side -- one per line the reader
    actually handed on. Eviction writes come from the producer and mean
    the CONSUMER has fallen behind, so counting them would let a wedged
    consumer look alive for as long as the agent kept talking.
    """
    reset_raw_overflow_path_state()
    log = get_or_create_raw_overflow_log(tmp_path, "liveness-unit")
    try:
        log.append("handed-on-by-the-consumer")
        after_consumer_write = log.size_bytes

        log.append("displaced-by-the-producer", counts_as_liveness=False)

        assert log.size_bytes == after_consumer_write
    finally:
        log.close()

    written = raw_log_path_for(tmp_path, "liveness-unit").read_text(encoding="utf-8")
    # Withheld from the liveness claim, NOT from the transcript.
    assert "displaced-by-the-producer" in written


def test_a_watchdog_fire_captures_the_pending_tail_exactly_once(tmp_path: Path) -> None:
    """The verbatim capture must not invent repetition.

    The drain path snapshots the queue and clears it, then writes that
    snapshot to the capture. When ``clear()`` ALSO routed its contents
    to the eviction sink, every line was written twice -- up to 256
    duplicated lines per fire, in exactly the region the transport
    failure message reads to explain a stall. A capture claiming the
    agent said something twice is not verbatim, and nothing downstream
    de-duplicates it.
    """
    from ralph.agents.execution_state import AgentExecutionState
    from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy, WatchdogVerdict

    reset_raw_overflow_path_state()
    clock = FakeClock()
    ctx = _make_subprocess_ctx(workspace_path=tmp_path)
    reader = ProcessLineReader(_FakeManagedProcess(), ctx, clock)

    tail = [f"tail-{index}" for index in range(5)]
    reader._lines_queue.extend(tail)

    watchdog = IdleWatchdog(TimeoutPolicy(idle_timeout_seconds=1.0), clock)
    watchdog.record_invocation_start()
    watchdog.record_any_output()
    # The watchdog opens a drain window before it fires, so step the
    # clock until it actually reaches FIRE rather than assuming one tick.
    verdict = WatchdogVerdict.CONTINUE
    for _ in range(20):
        clock.advance(120.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.RESUMABLE_CONTINUE)
        if verdict == WatchdogVerdict.FIRE:
            break
    assert verdict == WatchdogVerdict.FIRE, verdict

    # Production shape: the drain snapshots and clears the queue, and
    # the consumer generator then writes that snapshot to the capture
    # (``_process_reader`` lines 1105 / 1113 / 1125). Both halves must
    # run, because the duplication only shows when they are combined.
    fired = reader._check_fire(watchdog, verdict)
    assert fired is not None
    pending, _error = fired
    list(reader._capture_pending(pending))
    reader._raw_overflow.close()

    captured = _captured_lines(tmp_path, ctx.config)

    assert captured == tail
