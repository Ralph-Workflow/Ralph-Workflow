"""Tests for PlainLogRenderer phase counters and elapsed timing in [phase-close]."""

from __future__ import annotations

import re
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


def test_run_counters_accumulate_without_active_phase() -> None:
    """Run-level counters are updated even when _phase_counters is None."""
    renderer, buf = _make_renderer()
    # Emit activity without begin_phase
    renderer.emit_activity_line("u", "text", "content before phase")
    renderer.emit_activity_line("u", "tool_use", "bash")

    # Now begin phase and close it - run counters should have the pre-phase activity
    renderer.begin_phase("development")
    buf.truncate(0)
    buf.seek(0)
    renderer.emit_phase_close("development", "")
    out = buf.getvalue()
    # Both pre-phase and in-phase activity should be counted in run counters
    assert "content_blocks=1" in out
    assert "tool_calls=1" in out
