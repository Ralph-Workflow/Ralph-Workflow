"""Tests for ParallelDisplay's log-first output mode."""

from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from rich.console import Console

from ralph.agents.parsers.base import AgentParser
from ralph.display.activity_model import ActivityEventKind, ActivityProvider
from ralph.display.context import make_display_context
from ralph.display.parallel_display import (
    ParallelDisplay,
    strip_markup,
)
from ralph.display.phase_lifecycle import PhaseExitModel
from ralph.display.ring_buffer import RingBuffer
from ralph.pipeline.worker_state import WorkerStatus

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _make_wide_console() -> tuple[Console, StringIO]:
    buf = StringIO()
    # Width 1000 ensures condensation suffixes appear in output for soft-limit content
    console = Console(file=buf, force_terminal=False, width=1000, color_system=None)
    return console, buf


def test_ci_env_does_not_affect_width() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"CI": "1"})
    assert ctx.width == 120


def test_ci_empty_string_does_not_affect_width() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"CI": ""})
    assert ctx.width == 120


def test_no_color_env_does_not_affect_width() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"NO_COLOR": "1"})
    assert ctx.width == 120


def test_no_color_empty_string_does_not_affect_width() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"NO_COLOR": ""})
    assert ctx.width == 120


def test_term_dumb_does_not_affect_width() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"TERM": "dumb"})
    assert ctx.width == 120


def test_term_value_does_not_affect_width() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"TERM": "xterm-256color"})
    assert ctx.width == 120


def test_non_terminal_console_preserves_width() -> None:
    console = Console(force_terminal=False, width=120)
    ctx = make_display_context(console=console, env={})
    assert ctx.width == 120


def test_narrow_terminal_preserves_width() -> None:
    console = Console(force_terminal=True, width=40)
    ctx = make_display_context(console=console, env={})
    assert ctx.width == 40


@pytest.mark.parametrize("width", [40, 60, 80, 99, 100, 120, 200])
def test_any_terminal_width_preserves_width(width: int) -> None:
    """Any terminal width is preserved on the DisplayContext."""
    console = Console(force_terminal=True, width=width)
    ctx = make_display_context(console=console, env={})
    assert ctx.width == width


def test_parallel_display_initializes_with_console() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={})
    pd = ParallelDisplay(ctx)
    assert pd._ctx is ctx


def test_parallel_display_initializes_with_ci_env() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={"CI": "1"}))
    assert pd._ctx.width == 120


def test_parallel_display_default_env_preserves_width() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={}))
    assert pd._ctx.width == 120


def test_parallel_display_context_manager() -> None:
    console = Console(force_terminal=True, width=120)
    with ParallelDisplay(make_display_context(console=console, env={})) as pd:
        assert pd._ctx.width == 120


def test_parallel_display_emit_does_not_raise() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={}))
    pd.emit("unit-1", "some output line")


def test_parallel_display_emit_none_unit_id_does_not_raise() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={}))
    pd.emit(None, "some output line")


def test_parallel_display_set_status_writes_line() -> None:
    console = Console(force_terminal=True, width=120, record=True)
    pd = ParallelDisplay(make_display_context(console=console, env={}))
    pd.set_status("unit-1", WorkerStatus.RUNNING)
    text = console.export_text()
    assert "INFO" in text
    assert "META" in text
    assert "[status][unit-1]" in text
    assert "RUNNING" in text


def test_parallel_display_start_stop_do_not_raise() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={}))
    pd.start()
    pd.stop()


def test_parallel_display_default_mode_strips_rich_markup_and_streams_copy_pasteable_lines() -> None:
    console = Console(force_terminal=True, width=120, record=True)
    pd = ParallelDisplay(make_display_context(console=console, env={}))

    pd.start()
    try:
        pd.emit("unit-1", "[green]some output line[/green]")
    finally:
        pd.stop()

    rendered_text = console.export_text()
    assert "some output line" in rendered_text
    assert "[green]some output line[/green]" not in rendered_text
    assert "Agent Activity" not in rendered_text


def test_strip_markup_removes_rich_markup() -> None:
    assert strip_markup("[green]ok[/green]") == "ok"
    assert strip_markup("plain text") == "plain text"


# --- Raw overflow tests ---


def test_oversized_content_written_to_overflow_log(tmp_path: Path) -> None:
    console, _buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    big_content = "A" * 5000  # exceeds hard_limit=4000
    pd._emit_activity_event("unit-1", ActivityEventKind.TEXT, big_content, None, {})
    # The raw overflow log uses block buffering (RFC-013 P1); close() via
    # drop_unit() flushes the buffered tail to disk before the assertion.
    pd.drop_unit("unit-1")
    overflow_log = tmp_path / ".agent" / "raw" / "unit-1.log"
    assert overflow_log.exists(), "overflow log should be created for oversized content"
    written = overflow_log.read_text(encoding="utf-8")
    assert "A" * 100 in written


def test_tool_result_oversized_preserves_full_payload_in_overflow_log(tmp_path: Path) -> None:
    """Regression: TOOL_RESULT above soft_limit must capture the FULL payload in the overflow log.

    The analysis feedback flagged this regression: pre-fix the
    registry's ``_render_tool_result_event`` called an internal
    ``_condense_for_display`` helper that truncated the body to
    ``soft_limit`` characters BEFORE
    ``ParallelDisplay._emit_activity_event`` ran its overflow-aware
    condenser. A 1000-character tool result then landed in the
    overflow log as ~400 chars instead of 1000, silently truncating
    the audit trail.

    The fix moves condensation out of the renderer and into the
    delivery boundary (``_emit_activity_event``), so the overflow log
    captures the FULL unabridged line. This test pins the contract:
    every original character must appear in the on-disk overflow log.
    """
    console, buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    original_payload = "Z" * 1000  # above soft_limit(400), below hard_limit(4000)
    pd._emit_activity_event(
        "unit-tool-result",
        ActivityEventKind.TOOL_RESULT,
        original_payload,
        None,
        {},
    )
    # The raw overflow log uses block buffering; flush via drop_unit.
    pd.drop_unit("unit-tool-result")

    overflow_log = tmp_path / ".agent" / "raw" / "unit-tool-result.log"
    assert overflow_log.exists(), (
        f"overflow log should be created for the condensed tool result; "
        f"expected at {overflow_log}"
    )
    written = overflow_log.read_text(encoding="utf-8")
    z_count = written.count("Z")
    assert z_count == 1000, (
        f"overflow log must capture the FULL original tool result body; "
        f"got {z_count} Z chars in the overflow log, expected 1000. "
        f"Pre-fix regression: registry condenser truncated body to soft_limit "
        f"before overflow tracking, losing ~60% of the audit trail."
    )

    # Visible line must include the overflow reference and the
    # truncation marker so the operator knows where to find the
    # unabridged payload.
    rendered = buf.getvalue()
    assert "unit-tool-result.log" in rendered, (
        f"visible line must reference the overflow log path so the operator "
        f"can locate the unabridged payload; got: {rendered!r}"
    )
    assert "(truncated" in rendered, (
        f"visible line must carry the (truncated) marker; got: {rendered!r}"
    )


def test_soft_limit_content_overflow_ref_appears_in_output(tmp_path: Path) -> None:
    """Content between soft and hard limits includes overflow ref in condensed output."""
    console, buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    # 500 chars: above soft_limit(400), below hard_limit(4000)
    # renderer appends [see .agent/raw/unit-1.log] via condensed_ref
    soft_limit_content = "B" * 500
    pd._emit_activity_event("unit-1", ActivityEventKind.TEXT, soft_limit_content, None, {})

    rendered = buf.getvalue()
    assert "unit-1.log" in rendered


def test_condensed_ref_in_renderer_not_in_condenser(tmp_path: Path) -> None:
    """The overflow ref is added by PlainLogRenderer, not embedded in condenser output."""
    console, buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    soft_limit_content = "C" * 500
    pd._emit_activity_event("unit-1", ActivityEventKind.TEXT, soft_limit_content, None, {})

    rendered = buf.getvalue()
    # Renderer suffix uses [see ...] brackets
    assert "[see .agent/raw/unit-1.log]" in rendered
    # Condenser fallback "raw unavailable" should NOT appear since we don't pass overflow_ref
    assert "raw unavailable" not in rendered


def test_short_content_not_written_to_overflow(tmp_path: Path) -> None:
    console, _buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    small_content = "hello world"
    pd._emit_activity_event("unit-1", ActivityEventKind.TEXT, small_content, None, {})

    overflow_log = tmp_path / ".agent" / "raw" / "unit-1.log"
    assert not overflow_log.exists(), "short content should not trigger overflow log"


def test_stop_flushes_streaming_blocks(tmp_path: Path) -> None:
    console, buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    pd._emit_activity_event("unit-1", ActivityEventKind.TEXT, "partial output", None, {})
    pd.stop()

    rendered = buf.getvalue()
    assert "[content-end]" in rendered


def test_phase_close_from_exit_flushes_blocks(tmp_path: Path) -> None:

    console, buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    pd._emit_activity_event("unit-1", ActivityEventKind.TEXT, "some content", None, {})
    exit_model = PhaseExitModel(
        phase_name="planning",
        phase_role="planning",
        agent_name="planner",
        elapsed_seconds=1.0,
    )
    pd.emit_phase_close_from_exit(exit_model)

    rendered = buf.getvalue()
    assert "[content-end]" in rendered


# --- Drop reporting tests ---


def test_drop_warning_emitted_when_ring_buffer_drops(tmp_path: Path) -> None:
    """When the ring buffer drops lines, a WARN META [progress] line is emitted."""
    console, buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    # Inject a tiny ring buffer so we can force drops
    tiny_buf: RingBuffer = RingBuffer(maxsize=1)
    # Pre-fill to trigger drops
    tiny_buf.enqueue("existing")
    tiny_buf.enqueue("overflow-1")  # drops "existing", delta=1
    tiny_buf.enqueue("overflow-2")  # drops "overflow-1", delta=2

    pd._activity_router._buffers["unit-drop"] = tiny_buf

    # Trigger the event emission path which calls _emit_drop_warning
    pd._emit_activity_event("unit-drop", ActivityEventKind.TEXT, "new content", None, {})

    rendered = buf.getvalue()
    assert "dropped" in rendered
    assert "unit-drop" in rendered
    assert "WARN META [progress]" in rendered


def test_drop_warning_debounced_within_one_second(tmp_path: Path) -> None:
    """Two consecutive drop checks within 1 second produce only one warning."""
    console, buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    tiny_buf: RingBuffer = RingBuffer(maxsize=1)
    tiny_buf.enqueue("a")
    tiny_buf.enqueue("b")  # drops a, delta=1

    pd._activity_router._buffers["unit-x"] = tiny_buf

    # Force a drop warning to be emitted now
    with patch("ralph.display.parallel_display.time") as mock_time:
        mock_time.monotonic.return_value = 100.0
        pd._emit_activity_event("unit-x", ActivityEventKind.TEXT, "first", None, {})

    first_rendered = buf.getvalue()

    # Add more drops and try again within 1 second
    tiny_buf.enqueue("c")
    tiny_buf.enqueue("d")  # drops c, delta=1

    buf.truncate(0)
    buf.seek(0)

    with patch("ralph.display.parallel_display.time") as mock_time:
        mock_time.monotonic.return_value = 100.5  # still within debounce window
        pd._emit_activity_event("unit-x", ActivityEventKind.TEXT, "second", None, {})

    second_rendered = buf.getvalue()
    # First emission had a drop warning; second should NOT (still in debounce window)
    assert "dropped" in first_rendered
    assert "dropped" not in second_rendered


# --- Malformed input raw overflow tests ---


class _AlwaysRaisingParser(AgentParser):
    def parse(self, lines: Iterator[str]) -> Iterator[object]:
        raise ValueError("simulated parse failure")


def test_malformed_input_written_to_overflow_log(tmp_path: Path) -> None:
    """When ActivityRouter fails to parse a line, the raw input is written to overflow."""
    console, _buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    pd._activity_router._parser_factory = lambda _: _AlwaysRaisingParser()

    bad_line = '{"broken": true, "this_will_fail": }'
    pd._activity_router.push_raw_line("unit-bad", bad_line, provider=ActivityProvider.GENERIC)
    # Flush buffered tail to disk (RFC-013 P1).
    pd.drop_unit("unit-bad")

    overflow_log = tmp_path / ".agent" / "raw" / "unit-bad.log"
    assert overflow_log.exists(), "malformed line should be written to overflow log"
    content = overflow_log.read_text(encoding="utf-8")
    assert "broken" in content


def test_malformed_input_still_emits_error_event(tmp_path: Path) -> None:
    """A parse failure emits an ERROR event even when overflow write occurs."""
    console, buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    pd._activity_router._parser_factory = lambda _: _AlwaysRaisingParser()

    pd._activity_router.push_raw_line(
        "unit-bad2", "broken input", provider=ActivityProvider.GENERIC
    )

    rendered = buf.getvalue()
    assert "parser error" in rendered
