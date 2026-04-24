"""Tests for PlainLogRenderer phase counters and elapsed timing in [phase-close]."""

from __future__ import annotations

import re
import time
from io import StringIO

from rich.console import Console

from ralph.display.plain_renderer import PlainLogRenderer


def _make_renderer() -> tuple[PlainLogRenderer, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    return PlainLogRenderer(console), buf


def test_phase_close_contains_elapsed_and_counter_suffix() -> None:
    """begin_phase + activity + emit_phase_close includes elapsed= and all five counters."""
    renderer, buf = _make_renderer()
    renderer.begin_phase("development")
    renderer.emit_activity_line("u", "tool_use", "bash ls")
    renderer.emit_activity_line("u", "text", "hello world")
    buf.truncate(0)
    buf.seek(0)
    renderer.emit_phase_close("development", "result artifact present")
    out = buf.getvalue()
    # Verify suffix format
    assert re.search(r"elapsed=\d+(\.\d+)?s", out), f"Missing elapsed= in: {out}"
    assert "content_blocks=1" in out, f"content_blocks=1 missing in: {out}"
    assert "thinking_blocks=0" in out, f"thinking_blocks=0 missing in: {out}"
    assert "tool_calls=1" in out, f"tool_calls=1 missing in: {out}"
    assert "errors=0" in out, f"errors=0 missing in: {out}"


def test_phase_close_zero_activity_all_counters_zero() -> None:
    """A phase with no activity still emits all counters as zero."""
    renderer, buf = _make_renderer()
    renderer.begin_phase("planning")
    buf.truncate(0)
    buf.seek(0)
    renderer.emit_phase_close("planning", "")
    out = buf.getvalue()
    assert "content_blocks=0" in out
    assert "thinking_blocks=0" in out
    assert "tool_calls=0" in out
    assert "errors=0" in out
    assert "elapsed=0.0s" in out


def test_counters_reset_between_phases() -> None:
    """Counters reset to zero at the start of each new phase."""
    renderer, buf = _make_renderer()

    # First phase: one content block, one tool call
    renderer.begin_phase("a")
    renderer.emit_activity_line("u", "text", "content in a")
    renderer.emit_activity_line("u", "tool_use", "bash")
    buf.truncate(0)
    buf.seek(0)
    renderer.emit_phase_close("a", "phase a done")
    out_a = buf.getvalue()
    assert "content_blocks=1" in out_a
    assert "tool_calls=1" in out_a

    # Second phase: zero activity
    renderer.begin_phase("b")
    buf.truncate(0)
    buf.seek(0)
    renderer.emit_phase_close("b", "phase b done")
    out_b = buf.getvalue()
    assert "content_blocks=0" in out_b, f"Phase B should have 0 content_blocks: {out_b}"
    assert "tool_calls=0" in out_b, f"Phase B should have 0 tool_calls: {out_b}"
    assert "thinking_blocks=0" in out_b
    assert "errors=0" in out_b


def test_one_streaming_block_many_fragments_counts_as_one_content_block() -> None:
    """A streaming block with N fragments counts as exactly 1 content_block, not N."""
    renderer, buf = _make_renderer()
    renderer.begin_phase("development")
    # Emit many fragments in the same block
    for i in range(10):
        renderer.emit_activity_line("u", "text", f"fragment {i}")
    # emit_phase_close flushes the block and emits the phase line
    # The content_blocks=1 suffix reflects that these 10 fragments form ONE block
    buf.truncate(0)
    buf.seek(0)
    renderer.emit_phase_close("development", "")
    out = buf.getvalue()
    # content_blocks=1 because it's ONE streaming block (not 10)
    assert "content_blocks=1" in out, f"Expected content_blocks=1 but got: {out}"


def test_thinking_stream_counts_only_in_thinking_blocks() -> None:
    """Thinking events increment thinking_blocks, not content_blocks."""
    renderer, buf = _make_renderer()
    renderer.begin_phase("development")
    renderer.emit_activity_line("u", "thinking", "first thought")
    renderer.emit_activity_line("u", "thinking", "second thought")
    buf.truncate(0)
    buf.seek(0)
    renderer.emit_phase_close("development", "")
    out = buf.getvalue()
    assert "thinking_blocks=1" in out, f"Expected thinking_blocks=1: {out}"
    assert "content_blocks=0" in out, f"Expected content_blocks=0: {out}"


def test_error_kind_increments_error_counter() -> None:
    """Error kind increments errors counter."""
    renderer, buf = _make_renderer()
    renderer.begin_phase("development")
    renderer.emit_activity_line("u", "error", "something went wrong")
    buf.truncate(0)
    buf.seek(0)
    renderer.emit_phase_close("development", "")
    out = buf.getvalue()
    assert "errors=1" in out, f"Expected errors=1: {out}"


def test_emit_phase_close_without_begin_phase_still_emits_suffix() -> None:
    """emit_phase_close without prior begin_phase emits elapsed=0.0s and all counters zero."""
    renderer, buf = _make_renderer()
    # No begin_phase call — simulate calling emit_phase_close cold
    buf.truncate(0)
    buf.seek(0)
    renderer.emit_phase_close("planning", "")
    out = buf.getvalue()
    assert "elapsed=0.0s" in out, f"Expected elapsed=0.0s: {out}"
    assert "content_blocks=0" in out
    assert "thinking_blocks=0" in out
    assert "tool_calls=0" in out
    assert "errors=0" in out


def test_tool_use_counts_across_phases() -> None:
    """tool_use events in multiple phases accumulate in run counters but phase counters reset."""
    renderer, buf = _make_renderer()

    # Phase 1: 2 tool calls
    renderer.begin_phase("planning")
    renderer.emit_activity_line("u", "tool_use", "bash ls")
    renderer.emit_activity_line("u", "tool_use", "bash pwd")
    buf.truncate(0)
    buf.seek(0)
    renderer.emit_phase_close("planning", "")
    out1 = buf.getvalue()
    assert "tool_calls=2" in out1

    # Phase 2: 1 tool call
    renderer.begin_phase("development")
    renderer.emit_activity_line("u", "tool_use", "bash cat")
    buf.truncate(0)
    buf.seek(0)
    renderer.emit_phase_close("development", "")
    out2 = buf.getvalue()
    # Phase counters reset per phase
    assert "tool_calls=1" in out2


def test_pre_phase_activity_does_not_leak_into_first_phase_close() -> None:
    """Pre-phase activity shows up only in emit_run_end aggregates, never in first [phase-close]."""
    renderer, buf = _make_renderer()
    # Emit activity BEFORE any begin_phase call — this updates _run_counters only
    renderer.emit_activity_line("u", "text", "content before phase")
    renderer.emit_activity_line("u", "tool_use", "bash")

    # Start the first phase and close it immediately with no in-phase activity
    renderer.begin_phase("development")
    buf.truncate(0)
    buf.seek(0)
    renderer.emit_phase_close("development", "")
    phase_close_out = buf.getvalue()
    # First [phase-close] must report ZERO for everything because no in-phase activity
    assert "content_blocks=0" in phase_close_out, (
        f"first phase must not inherit pre-phase counters, got: {phase_close_out}"
    )
    assert "tool_calls=0" in phase_close_out
    assert "thinking_blocks=0" in phase_close_out
    assert "errors=0" in phase_close_out

    # emit_run_end must still reflect the pre-phase activity in aggregate counters
    buf.truncate(0)
    buf.seek(0)
    renderer.emit_run_end(phase="complete", total_agent_calls=0)
    run_end_out = buf.getvalue()
    assert "content_blocks=1" in run_end_out, (
        f"run-end must aggregate pre-phase activity, got: {run_end_out}"
    )
    assert "tool_calls=1" in run_end_out


def test_plain_renderer_exposes_run_counter_properties() -> None:
    """Public properties expose run-level counter values for the completion panel."""
    renderer, _buf = _make_renderer()
    renderer.begin_phase("development")

    # Two text fragments from the same unit form ONE streaming block.
    # Switching to a different kind (thinking) closes the text block and opens a new one.
    renderer.emit_activity_line("u", "text", "content block 1")
    renderer.emit_activity_line("u", "text", "content block 2")  # same block, no new count
    renderer.emit_activity_line("u", "thinking", "thinking 1")  # closes text, opens thinking
    renderer.emit_activity_line("u", "tool_use", "bash ls")
    renderer.emit_activity_line("u", "tool_use", "bash pwd")
    renderer.emit_activity_line("u", "error", "some error")

    # Check public read-only properties
    assert renderer.content_blocks_count == 1, (
        f"Expected content_blocks_count=1, got {renderer.content_blocks_count}"
    )
    assert renderer.thinking_blocks_count == 1, (
        f"Expected thinking_blocks_count=1, got {renderer.thinking_blocks_count}"
    )
    assert renderer.tool_calls_count == 2, (  # noqa: PLR2004
        f"Expected tool_calls_count=2, got {renderer.tool_calls_count}"
    )
    assert renderer.errors_count == 1, (
        f"Expected errors_count=1, got {renderer.errors_count}"
    )

    elapsed = renderer.run_elapsed_seconds
    assert elapsed is not None, "run_elapsed_seconds should not be None after begin_phase"
    assert elapsed >= 0.0, f"run_elapsed_seconds should be non-negative, got {elapsed}"


def test_run_elapsed_seconds_is_none_before_run_start() -> None:
    """run_elapsed_seconds returns None when no run has started."""
    renderer, _buf = _make_renderer()
    assert renderer.run_elapsed_seconds is None


def test_run_elapsed_seconds_reflects_elapsed_time() -> None:
    """run_elapsed_seconds returns elapsed time since run start."""
    renderer, _buf = _make_renderer()
    renderer.begin_phase("development")
    # Small delay to ensure elapsed time > 0
    time.sleep(0.01)
    elapsed = renderer.run_elapsed_seconds
    assert elapsed is not None
    assert elapsed >= 0.01, f"Expected elapsed >= 0.01s, got {elapsed}"  # noqa: PLR2004
