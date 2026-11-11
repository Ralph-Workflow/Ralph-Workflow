from __future__ import annotations

import re
from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay


def _make_display() -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    return ParallelDisplay(make_display_context(console=console, env={})), buf


def test_phase_close_contains_elapsed_and_counter_suffix() -> None:
    pd, buf = _make_display()
    pd.begin_phase("development")
    pd.emit_activity_line("u", "tool_use", "bash ls")
    pd.emit_activity_line("u", "text", "hello world")
    buf.truncate(0)
    buf.seek(0)
    pd.emit_phase_close("development", "result artifact present")
    out = buf.getvalue()
    assert re.search(r"elapsed=\d+(\.\d+)?s", out), f"Missing elapsed= in: {out}"
    assert "content_blocks=1" in out, f"content_blocks=1 missing in: {out}"
    assert "thinking_blocks=0" in out, f"thinking_blocks=0 missing in: {out}"
    assert "tool_calls=1" in out, f"tool_calls=1 missing in: {out}"
    assert "errors=0" in out, f"errors=0 missing in: {out}"


def test_phase_close_zero_activity_all_counters_zero() -> None:
    pd, buf = _make_display()
    pd.begin_phase("planning")
    buf.truncate(0)
    buf.seek(0)
    pd.emit_phase_close("planning", "")
    out = buf.getvalue()
    assert "content_blocks=0" in out
    assert "thinking_blocks=0" in out
    assert "tool_calls=0" in out
    assert "errors=0" in out
    assert re.search(r"elapsed=\d+(?:\.\d+)?s", out)


def test_counters_reset_between_phases() -> None:
    pd, buf = _make_display()

    pd.begin_phase("a")
    pd.emit_activity_line("u", "text", "content in a")
    pd.emit_activity_line("u", "tool_use", "bash")
    buf.truncate(0)
    buf.seek(0)
    pd.emit_phase_close("a", "phase a done")
    out_a = buf.getvalue()
    assert "content_blocks=1" in out_a
    assert "tool_calls=1" in out_a

    pd.begin_phase("b")
    buf.truncate(0)
    buf.seek(0)
    pd.emit_phase_close("b", "phase b done")
    out_b = buf.getvalue()
    assert "content_blocks=0" in out_b, f"Phase B should have 0 content_blocks: {out_b}"
    assert "tool_calls=0" in out_b, f"Phase B should have 0 tool_calls: {out_b}"
    assert "thinking_blocks=0" in out_b
    assert "errors=0" in out_b


def test_one_streaming_block_many_fragments_counts_as_one_content_block() -> None:
    pd, buf = _make_display()
    pd.begin_phase("development")
    for i in range(10):
        pd.emit_activity_line("u", "text", f"fragment {i}")
    buf.truncate(0)
    buf.seek(0)
    pd.emit_phase_close("development", "")
    out = buf.getvalue()
    assert "content_blocks=1" in out, f"Expected content_blocks=1 but got: {out}"


def test_thinking_stream_counts_only_in_thinking_blocks() -> None:
    pd, buf = _make_display()
    pd.begin_phase("development")
    pd.emit_activity_line("u", "thinking", "first thought")
    pd.emit_activity_line("u", "thinking", "second thought")
    buf.truncate(0)
    buf.seek(0)
    pd.emit_phase_close("development", "")
    out = buf.getvalue()
    assert "thinking_blocks=1" in out, f"Expected thinking_blocks=1: {out}"
    assert "content_blocks=0" in out, f"Expected content_blocks=0: {out}"


def test_error_kind_increments_error_counter() -> None:
    pd, buf = _make_display()
    pd.begin_phase("development")
    pd.emit_activity_line("u", "error", "something went wrong")
    buf.truncate(0)
    buf.seek(0)
    pd.emit_phase_close("development", "")
    out = buf.getvalue()
    assert "errors=1" in out, f"Expected errors=1: {out}"


def test_emit_phase_close_without_begin_phase_still_emits_suffix() -> None:
    pd, buf = _make_display()
    buf.truncate(0)
    buf.seek(0)
    pd.emit_phase_close("planning", "")
    out = buf.getvalue()
    assert "elapsed=0s" in out, f"Expected elapsed=0s: {out}"
    assert "content_blocks=0" in out
    assert "thinking_blocks=0" in out
    assert "tool_calls=0" in out
    assert "errors=0" in out


def test_tool_use_counts_across_phases() -> None:
    pd, buf = _make_display()

    pd.begin_phase("planning")
    pd.emit_activity_line("u", "tool_use", "bash ls")
    pd.emit_activity_line("u", "tool_use", "bash pwd")
    buf.truncate(0)
    buf.seek(0)
    pd.emit_phase_close("planning", "")
    out1 = buf.getvalue()
    assert "tool_calls=2" in out1

    pd.begin_phase("development")
    pd.emit_activity_line("u", "tool_use", "bash cat")
    buf.truncate(0)
    buf.seek(0)
    pd.emit_phase_close("development", "")
    out2 = buf.getvalue()
    assert "tool_calls=1" in out2


def test_pre_phase_activity_does_not_leak_into_first_phase_close() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "content before phase")
    pd.emit_activity_line("u", "tool_use", "bash")

    pd.begin_phase("development")
    buf.truncate(0)
    buf.seek(0)
    pd.emit_phase_close("development", "")
    phase_close_out = buf.getvalue()
    assert "content_blocks=0" in phase_close_out, (
        f"first phase must not inherit pre-phase counters, got: {phase_close_out}"
    )
    assert "tool_calls=0" in phase_close_out
    assert "thinking_blocks=0" in phase_close_out
    assert "errors=0" in phase_close_out

    buf.truncate(0)
    buf.seek(0)
    pd.emit_run_end(phase="complete", total_agent_calls=0)
    run_end_out = buf.getvalue()
    assert "content_blocks=1" in run_end_out, (
        f"run-end must aggregate pre-phase activity, got: {run_end_out}"
    )
    assert "tool_calls=1" in run_end_out


def test_plain_renderer_exposes_run_counter_properties() -> None:
    pd, _buf = _make_display()
    pd.begin_phase("development")

    pd.emit_activity_line("u", "text", "content block 1")
    pd.emit_activity_line("u", "text", "content block 2")
    pd.emit_activity_line("u", "thinking", "thinking 1")
    pd.emit_activity_line("u", "tool_use", "bash ls")
    pd.emit_activity_line("u", "tool_use", "bash pwd")
    pd.emit_activity_line("u", "error", "some error")

    assert pd.content_blocks_count == 1, (
        f"Expected content_blocks_count=1, got {pd.content_blocks_count}"
    )
    assert pd.thinking_blocks_count == 1, (
        f"Expected thinking_blocks_count=1, got {pd.thinking_blocks_count}"
    )
    assert pd.tool_calls_count == 2, f"Expected tool_calls_count=2, got {pd.tool_calls_count}"
    assert pd.errors_count == 1, f"Expected errors_count=1, got {pd.errors_count}"

    elapsed = pd.run_elapsed_seconds
    assert elapsed is not None, "run_elapsed_seconds should not be None after begin_phase"
    assert elapsed >= 0.0, f"run_elapsed_seconds should be non-negative, got {elapsed}"


def test_run_elapsed_seconds_is_none_before_run_start() -> None:
    pd, _buf = _make_display()
    assert pd.run_elapsed_seconds is None


def test_run_elapsed_seconds_reflects_elapsed_time() -> None:
    fake_elapsed = 5.0
    fake_time = [0.0]
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    renderer = ParallelDisplay(
        make_display_context(console=console, env={}),
        monotonic=lambda: fake_time[0],
    )
    renderer.begin_phase("development")
    fake_time[0] = fake_elapsed
    elapsed = renderer.run_elapsed_seconds
    assert elapsed is not None
    assert elapsed == fake_elapsed, f"Expected elapsed == {fake_elapsed}s, got {elapsed}"


def test_last_phase_counters_preserved_after_emit_phase_close() -> None:
    pd, _buf = _make_display()
    pd.begin_phase("development")
    pd.emit_activity_line("u", "text", "content")
    pd.emit_activity_line("u", "tool_use", "bash ls")
    pd.emit_phase_close("development", "result artifact present")
    counters = pd.last_phase_counters
    assert counters is not None, "last_phase_counters must not be None after emit_phase_close"
    assert counters.content_blocks == 1
    assert counters.tool_calls == 1


def test_last_phase_counters_none_before_any_phase_close() -> None:
    pd, _buf = _make_display()
    assert pd.last_phase_counters is None


def test_last_phase_counters_updated_by_second_close() -> None:
    pd, _buf = _make_display()
    pd.begin_phase("planning")
    pd.emit_activity_line("u", "tool_use", "bash ls")
    pd.emit_phase_close("planning", "")
    pd.begin_phase("development")
    pd.emit_activity_line("u", "text", "some content")
    pd.emit_phase_close("development", "result artifact present")
    counters = pd.last_phase_counters
    assert counters is not None
    assert counters.content_blocks == 1
    assert counters.tool_calls == 0
