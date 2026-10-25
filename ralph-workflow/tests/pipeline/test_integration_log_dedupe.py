"""Outcome-once logging for the integration owner path (log-spam rule).

A repeated identical integration outcome must produce one user-visible
line plus a rate-limited count, not one CRITICAL per retry tick. These
tests drive the dedupe helper with a fake clock and the run-loop retry
seam with a captured loguru sink; no test sleeps or touches the
filesystem.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from loguru import logger

from ralph.pipeline import run_loop
from ralph.pipeline._integration_log_dedupe import IntegrationOutcomeLog
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState


class FakeSeconds:
    """Injectable monotonic clock; advances only when the test says so."""

    def __init__(self) -> None:
        self._now = 0.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class _Collector:
    """Typed emit sink so both type checkers accept it as OutcomeEmit."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def emit(self, message: str) -> None:
        self.lines.append(message)


def _collect() -> tuple[list[str], int]:
    lines: list[str] = []
    sink_id = logger.add(lines.append, level="CRITICAL", format="{message}")
    return lines, sink_id


def test_identical_outcome_emits_one_line_for_n_repeats() -> None:
    """N identical repeats within the interval -> 1 line, then a count."""
    clock = FakeSeconds()
    log = IntegrationOutcomeLog(clock=clock, repeat_log_interval_seconds=300.0)
    collector = _Collector()

    assert log.record(collector.emit, "CRITICAL: integration has not landed") is True
    for _ in range(9):
        assert log.record(collector.emit, "CRITICAL: integration has not landed") is False
    clock.advance(301.0)
    assert log.record(collector.emit, "CRITICAL: integration has not landed") is True

    assert collector.lines == [
        "CRITICAL: integration has not landed",
        "CRITICAL: integration has not landed (repeated 11x since the last update)",
    ]


def test_distinct_outcomes_each_emit_immediately() -> None:
    """A changed outcome is a different line and logs at once."""
    clock = FakeSeconds()
    log = IntegrationOutcomeLog(clock=clock, repeat_log_interval_seconds=300.0)
    collector = _Collector()
    log.record(collector.emit, "outcome A")
    log.record(collector.emit, "outcome B")
    # Interleaving another outcome does not reset A's rate limit: an
    # identical repeat is counted even when other lines came between.
    assert log.record(collector.emit, "outcome A") is False
    assert collector.lines == ["outcome A", "outcome B"]


def test_summary_resets_the_count_for_the_next_interval() -> None:
    """Each rate-limited summary carries only the repeats since the last line."""
    clock = FakeSeconds()
    log = IntegrationOutcomeLog(clock=clock, repeat_log_interval_seconds=10.0)
    collector = _Collector()
    message = "held"
    log.record(collector.emit, message)
    for _ in range(4):
        log.record(collector.emit, message)
    clock.advance(11.0)
    log.record(collector.emit, message)  # summary: 5x total, 4 suppressed
    for _ in range(2):
        log.record(collector.emit, message)
    clock.advance(11.0)
    log.record(collector.emit, message)  # summary counts only the new repeats
    assert collector.lines == [
        "held",
        "held (repeated 6x since the last update)",
        "held (repeated 4x since the last update)",
    ]


def test_the_record_map_is_bounded_and_evicts_oldest() -> None:
    """More distinct outcomes than the cap evict the oldest, not growth."""
    log = IntegrationOutcomeLog(repeat_log_interval_seconds=300.0, max_entries=2)
    collector = _Collector()
    log.record(collector.emit, "one")
    log.record(collector.emit, "two")
    log.record(collector.emit, "three")  # evicts "one"
    assert log.record(collector.emit, "one") is True  # evicted: logs again
    assert collector.lines == ["one", "two", "three", "one"]


def test_invalid_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="repeat_log_interval_seconds"):
        IntegrationOutcomeLog(repeat_log_interval_seconds=0.0)
    with pytest.raises(ValueError, match="max_entries"):
        IntegrationOutcomeLog(max_entries=0)


def test_retry_wait_critical_is_logged_once_for_an_unchanged_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run-loop retry seam dedupes the identical CRITICAL per tick.

    Two retry waits against the same unresolved observation must produce
    one CRITICAL line; the run loop's recovery behaviour (waiting, state
    carry) is unchanged -- only the emission is counted.
    """
    from ralph.config.models import UnifiedConfig

    clock = FakeSeconds()
    outcome_log = IntegrationOutcomeLog(clock=clock, repeat_log_interval_seconds=300.0)
    monkeypatch.setattr(run_loop, "integration_outcome_log", lambda: outcome_log)

    def _pending(
        state: PipelineState,
        _ctx: object,
        _rebase: RebaseState,
        _detail: str,
        _backend: object,
    ) -> PipelineState:
        return state

    def _noop_save(_state: PipelineState, _ctx: run_loop._LoopContext) -> None:
        return None

    def _noop_warn(_display: object, _message: str) -> None:
        return None

    monkeypatch.setattr(run_loop, "_handle_pending_retry", _pending)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)

    latest_state: list[PipelineState] = []
    ctx = cast(
        "run_loop._LoopContext",
        SimpleNamespace(
            active_display=SimpleNamespace(emit_warn_line=_noop_warn),
            config=UnifiedConfig(),
            workspace_scope=SimpleNamespace(root=Path("/unused")),
            latest_state=latest_state,
        ),
    )
    state = PipelineState(
        phase="development",
        rebase=RebaseState(last_action="conflict", last_reason="unresolved shared.txt"),
    )

    lines, sink_id = _collect()
    try:
        run_loop._wait_for_integration_retry(state, ctx)
        run_loop._wait_for_integration_retry(state, ctx)
        assert len(lines) == 1, lines
        assert "integration has not landed" in lines[0]
        # After the rate-limit interval the retry surfaces one summary
        # line carrying the suppressed count, not a wall of repeats.
        clock.advance(301.0)
        run_loop._wait_for_integration_retry(state, ctx)
        assert len(lines) == 2, lines
        assert "repeated 3x since the last update" in lines[1]
        # A changed observation is a new outcome and logs immediately.
        changed = state.copy_with(
            rebase=RebaseState(last_action="conflict", last_reason="diverged target.txt")
        )
        run_loop._wait_for_integration_retry(changed, ctx)
        assert len(lines) == 3, lines
        assert "diverged target.txt" in lines[2]
    finally:
        logger.remove(sink_id)
