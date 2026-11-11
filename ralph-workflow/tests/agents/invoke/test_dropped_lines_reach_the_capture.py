from __future__ import annotations

import os
from dataclasses import replace
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
_BURST = 1000


def _captured_lines(workspace: Path, reader_config: AgentConfig) -> list[str]:
    path = raw_log_path_for(workspace, raw_log_unit_id_for(reader_config))
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def test_a_line_queued_as_the_loop_exits_still_reaches_the_capture(
    tmp_path: Path,
) -> None:
    reset_raw_overflow_path_state()
    ctx = _make_subprocess_ctx(workspace_path=tmp_path)
    reader: ProcessLineReader | None = None

    def terminal_and_append() -> bool:
        assert reader is not None
        reader._lines_queue.append("late-frame")
        return True

    ctx = replace(ctx, completion_is_terminal=terminal_and_append)
    reader = ProcessLineReader(_FakeManagedProcess(), ctx, FakeClock(start=0.0))

    yielded = list(reader.read_lines())

    captured = _captured_lines(tmp_path, ctx.config)
    assert "late-frame" in captured, captured
    assert "late-frame" not in yielded


def test_a_process_reader_burst_loses_no_line(tmp_path: Path) -> None:
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
    assert "displaced-by-the-producer" in written


def test_a_watchdog_fire_captures_the_pending_tail_exactly_once(tmp_path: Path) -> None:
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
    verdict = WatchdogVerdict.CONTINUE
    for _ in range(20):
        clock.advance(120.0)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.RESUMABLE_CONTINUE)
        if verdict == WatchdogVerdict.FIRE:
            break
    assert verdict == WatchdogVerdict.FIRE, verdict

    fired = reader._check_fire(watchdog, verdict)
    assert fired is not None
    pending, _error = fired
    list(reader._capture_pending(pending))
    reader._raw_overflow.close()

    captured = _captured_lines(tmp_path, ctx.config)

    assert captured == tail
