"""Tests for ParallelDisplay's log-first output mode."""

from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING
from unittest.mock import patch

from rich.console import Console

from ralph.agents.parsers.base import AgentParser
from ralph.display.activity_model import ActivityEventKind, ActivityProvider
from ralph.display.context import make_display_context
from ralph.display.mode import MEDIUM_THRESHOLD, NARROW_THRESHOLD
from ralph.display.parallel_display import (
    ParallelDisplay,
    _strip_markup,
)
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


def test_ci_env_does_not_affect_mode() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"CI": "1"})
    assert ctx.mode == "wide"


def test_ci_empty_string_does_not_affect_mode() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"CI": ""})
    assert ctx.mode == "wide"


def test_no_color_env_does_not_affect_mode() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"NO_COLOR": "1"})
    assert ctx.mode == "wide"


def test_no_color_empty_string_does_not_affect_mode() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"NO_COLOR": ""})
    assert ctx.mode == "wide"


def test_term_dumb_does_not_affect_mode() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"TERM": "dumb"})
    assert ctx.mode == "wide"


def test_term_value_does_not_affect_mode() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"TERM": "xterm-256color"})
    assert ctx.mode == "wide"


def test_non_terminal_console_does_not_affect_mode() -> None:
    console = Console(force_terminal=False, width=120)
    ctx = make_display_context(console=console, env={})
    assert ctx.mode == "wide"


def test_narrow_terminal_returns_compact() -> None:
    console = Console(force_terminal=True, width=40)
    ctx = make_display_context(console=console, env={})
    assert ctx.mode == "compact"


def test_threshold_boundary_returns_medium() -> None:
    console = Console(force_terminal=True, width=NARROW_THRESHOLD)
    ctx = make_display_context(console=console, env={})
    assert ctx.mode == "medium"


def test_threshold_plus_one_returns_medium() -> None:
    console = Console(force_terminal=True, width=NARROW_THRESHOLD + 1)
    ctx = make_display_context(console=console, env={})
    assert ctx.mode == "medium"


def test_threshold_99_returns_medium() -> None:
    console = Console(force_terminal=True, width=MEDIUM_THRESHOLD - 1)
    ctx = make_display_context(console=console, env={})
    assert ctx.mode == "medium"


def test_threshold_60_returns_medium() -> None:
    console = Console(force_terminal=True, width=60)
    ctx = make_display_context(console=console, env={})
    assert ctx.mode == "medium"


def test_threshold_100_returns_wide() -> None:
    console = Console(force_terminal=True, width=MEDIUM_THRESHOLD)
    ctx = make_display_context(console=console, env={})
    assert ctx.mode == "wide"


def test_parallel_display_mode_detected_at_init() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={})
    pd = ParallelDisplay(make_display_context(console=console, env={}))
    assert pd.mode == ctx.mode == "wide"


def test_parallel_display_mode_wide_when_ci() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={"CI": "1"}))
    assert pd.mode == "wide"


def test_parallel_display_default_env_uses_os_environ() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={}))
    assert pd.mode in ("compact", "medium", "wide")


def test_parallel_display_mode_frozen_after_init() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={}))
    try:
        pd.mode = "lines"  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        raise AssertionError("Should have raised AttributeError")
    except AttributeError:
        pass


def test_parallel_display_context_manager() -> None:
    console = Console(force_terminal=True, width=120)
    with ParallelDisplay(make_display_context(console=console, env={})) as pd:
        assert pd.mode == "wide"


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
    assert "status=RUNNING" in console.export_text()


def test_parallel_display_start_stop_do_not_raise() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={}))
    pd.start()
    pd.stop()


def test_parallel_display_default_mode_streams_copy_pasteable_lines() -> None:
    console = Console(force_terminal=True, width=120, record=True)
    pd = ParallelDisplay(make_display_context(console=console, env={}))

    assert pd.mode == "wide"

    pd.start()
    try:
        pd.emit("unit-1", "[green]some output line[/green]")
    finally:
        pd.stop()

    rendered_text = console.export_text()
    assert "some output line" in rendered_text
    assert "[green]" not in rendered_text
    assert "[/green]" not in rendered_text
    assert "Agent Activity" not in rendered_text


def test_strip_markup_removes_rich_tags() -> None:
    assert _strip_markup("[green]ok[/green]") == "ok"
    assert _strip_markup("plain text") == "plain text"


# --- Raw overflow tests ---


def test_oversized_content_written_to_overflow_log(tmp_path: Path) -> None:
    console, _buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    big_content = "A" * 5000  # exceeds hard_limit=4000
    pd._emit_activity_event("unit-1", ActivityEventKind.TEXT, big_content, None, {})
    overflow_log = tmp_path / ".agent" / "raw" / "unit-1.log"
    assert overflow_log.exists(), "overflow log should be created for oversized content"
    written = overflow_log.read_text(encoding="utf-8")
    assert "A" * 100 in written


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


def test_emit_phase_transition_flushes_blocks(tmp_path: Path) -> None:
    console, buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    pd._emit_activity_event("unit-1", ActivityEventKind.TEXT, "some content", None, {})
    pd.emit_phase_transition("planning", "development")

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
    def parse(self, lines: Iterator[str]) -> Iterator[object]:  # type: ignore[override]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        raise ValueError("simulated parse failure")


def test_malformed_input_written_to_overflow_log(tmp_path: Path) -> None:
    """When ActivityRouter fails to parse a line, the raw input is written to overflow."""
    console, _buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    pd._activity_router._parser_factory = lambda _: _AlwaysRaisingParser()

    bad_line = '{"broken": true, "this_will_fail": }'
    pd._activity_router.push_raw_line("unit-bad", bad_line, provider=ActivityProvider.GENERIC)

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
