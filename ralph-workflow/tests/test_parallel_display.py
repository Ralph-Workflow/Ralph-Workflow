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
    console = Console(force_terminal=True, width=width)
    ctx = make_display_context(console=console, env={})
    assert ctx.width == width


@pytest.mark.parametrize(
    ("body", "expected"),
    (
        ("界界界 x", "界界界\nx"),
        ("café café café", "café\ncafé\ncafé"),
        ("PASS read_file", "PASS\nread_file"),
        ("line one\n\nline two", "line\none\n\nline\ntwo"),
    ),
)
def test_parallel_display_regression_wraps_body_by_display_cells(body: str, expected: str) -> None:
    wrapped = ParallelDisplay._wrap_body_with_hanging_indent(
        "00:00:00 [x] ", body, total_width=20, body_measure=20
    )
    assert wrapped == expected


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
    assert "INFO" not in text
    assert "META" not in text
    assert "[status][unit-1]" in text
    assert "RUNNING" in text


def test_parallel_display_start_stop_do_not_raise() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={}))
    pd.start()
    pd.stop()


def test_parallel_display_default_mode_reduces_rich_markup_and_streams_copy_pasteable_lines() -> (
    None
):
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


def test_strip_markup_strips_valid_markup_and_preserves_literal_brackets() -> None:
    assert strip_markup("[green]ok[/green]") == "ok"
    assert strip_markup("[see foo/bar") == "[see foo/bar"
    assert strip_markup("plain text") == "plain text"


def test_oversized_content_written_to_overflow_log(tmp_path: Path) -> None:
    console, _buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    big_content = "A" * 5000
    pd._emit_activity_event("unit-1", ActivityEventKind.TEXT, big_content, None, {})
    pd.drop_unit("unit-1")
    overflow_log = tmp_path / ".agent" / "raw" / "unit-1.overflow.log"
    assert overflow_log.exists(), "overflow log should be created for oversized content"
    written = overflow_log.read_text(encoding="utf-8")
    assert "A" * 100 in written


def test_oversized_text_preserves_full_payload_in_overflow_log(tmp_path: Path) -> None:
    console, _buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)
    original_payload = "界" * 3_000

    pd._emit_activity_event("unit-hard-limit", ActivityEventKind.TEXT, original_payload, None, {})
    pd.drop_unit("unit-hard-limit")

    written = (tmp_path / ".agent" / "raw" / "unit-hard-limit.overflow.log").read_text(
        encoding="utf-8"
    )
    assert original_payload in written


def test_tool_result_oversized_preserves_full_payload_in_overflow_log(tmp_path: Path) -> None:
    console, buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    original_payload = "Z" * 1000
    pd._emit_activity_event(
        "unit-tool-result",
        ActivityEventKind.TOOL_RESULT,
        original_payload,
        None,
        {},
    )
    pd.drop_unit("unit-tool-result")

    overflow_log = tmp_path / ".agent" / "raw" / "unit-tool-result.overflow.log"
    assert overflow_log.exists(), (
        f"overflow log should be created for the condensed tool result; expected at {overflow_log}"
    )
    written = overflow_log.read_text(encoding="utf-8")
    z_count = written.count("Z")
    assert z_count == 1000, (
        f"overflow log must capture the FULL original tool result body; "
        f"got {z_count} Z chars in the overflow log, expected 1000. "
        f"Pre-fix regression: registry condenser truncated body to soft_limit "
        f"before overflow tracking, losing ~60% of the audit trail."
    )

    rendered = buf.getvalue()
    assert "unit-tool-result.overflow.log" in rendered, (
        f"visible line must reference the overflow log path so the operator "
        f"can locate the unabridged payload; got: {rendered!r}"
    )
    assert "(truncated" in rendered, (
        f"visible line must carry the (truncated) marker; got: {rendered!r}"
    )


def test_soft_limit_content_overflow_ref_appears_in_output(tmp_path: Path) -> None:
    console, buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    soft_limit_content = "B" * 500
    pd._emit_activity_event("unit-1", ActivityEventKind.TEXT, soft_limit_content, None, {})
    pd.stop()

    rendered = buf.getvalue()
    assert "[output][unit-1]" in rendered
    assert "see .agent/raw/unit-1.overflow.log" in rendered
    assert soft_limit_content not in rendered
    content_rows = [line for line in rendered.splitlines() if "[output][unit-1]" in line]
    assert len(content_rows) == 1, f"expected one close row:\n{rendered}"


def test_condensed_ref_in_renderer_not_in_condenser(tmp_path: Path) -> None:
    console, buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    soft_limit_content = "C" * 500
    pd._emit_activity_event("unit-1", ActivityEventKind.TEXT, soft_limit_content, None, {})
    pd.stop()

    rendered = buf.getvalue()
    assert "see .agent/raw/unit-1.overflow.log" in rendered
    assert soft_limit_content not in rendered
    assert "[see .agent/raw/" not in rendered


def test_short_content_not_written_to_overflow(tmp_path: Path) -> None:
    console, _buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    small_content = "hello world"
    pd._emit_activity_event("unit-1", ActivityEventKind.TEXT, small_content, None, {})

    overflow_log = tmp_path / ".agent" / "raw" / "unit-1.overflow.log"
    assert not overflow_log.exists(), "short content should not trigger overflow log"


def test_stop_flushes_streaming_blocks(tmp_path: Path) -> None:
    console, buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    pd._emit_activity_event("unit-1", ActivityEventKind.TEXT, "partial output", None, {})
    pd.stop()

    rendered = buf.getvalue()
    assert "[output][unit-1]" in rendered


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
    assert "[output][unit-1]" in rendered


def test_drop_warning_emitted_when_ring_buffer_drops(tmp_path: Path) -> None:
    console, buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    tiny_buf: RingBuffer = RingBuffer(maxsize=1)
    tiny_buf.enqueue("existing")
    tiny_buf.enqueue("overflow-1")
    tiny_buf.enqueue("overflow-2")

    pd._activity_router._buffers["unit-drop"] = tiny_buf

    pd._emit_activity_event("unit-drop", ActivityEventKind.TEXT, "new content", None, {})

    rendered = buf.getvalue()
    assert "dropped" in rendered
    assert "unit-drop" in rendered
    assert "WARN META" not in rendered
    assert "[progress]" in rendered
    assert "dropped" in rendered


def test_drop_warning_debounced_within_one_second(tmp_path: Path) -> None:
    console, buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    tiny_buf: RingBuffer = RingBuffer(maxsize=1)
    tiny_buf.enqueue("a")
    tiny_buf.enqueue("b")

    pd._activity_router._buffers["unit-x"] = tiny_buf

    with patch("ralph.display.parallel_display.time") as mock_time:
        mock_time.monotonic.return_value = 100.0
        pd._emit_activity_event("unit-x", ActivityEventKind.TEXT, "first", None, {})

    first_rendered = buf.getvalue()

    tiny_buf.enqueue("c")
    tiny_buf.enqueue("d")

    buf.truncate(0)
    buf.seek(0)

    with patch("ralph.display.parallel_display.time") as mock_time:
        mock_time.monotonic.return_value = 100.5
        pd._emit_activity_event("unit-x", ActivityEventKind.TEXT, "second", None, {})

    second_rendered = buf.getvalue()
    assert "dropped" in first_rendered
    assert "dropped" not in second_rendered


class _AlwaysRaisingParser(AgentParser):
    def parse(self, lines: Iterator[str]) -> Iterator[object]:
        raise ValueError("simulated parse failure")


def test_malformed_input_written_to_overflow_log(tmp_path: Path) -> None:
    console, _buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    pd._activity_router._parser_factory = lambda _: _AlwaysRaisingParser()

    bad_line = '{"broken": true, "this_will_fail": }'
    pd._activity_router.push_raw_line("unit-bad", bad_line, provider=ActivityProvider.GENERIC)
    pd.drop_unit("unit-bad")

    overflow_log = tmp_path / ".agent" / "raw" / "unit-bad.overflow.log"
    assert overflow_log.exists(), "malformed line should be written to overflow log"
    content = overflow_log.read_text(encoding="utf-8")
    assert "broken" in content


def test_malformed_input_still_emits_error_event(tmp_path: Path) -> None:
    console, buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    pd._activity_router._parser_factory = lambda _: _AlwaysRaisingParser()

    pd._activity_router.push_raw_line(
        "unit-bad2", "broken input", provider=ActivityProvider.GENERIC
    )

    rendered = buf.getvalue()
    assert "parser error" in rendered


def test_parallel_display_wide_chrome_prefix_does_not_collapse_to_single_char_rows() -> None:
    prefix = "03:18:03 [output][claude-headless/haiku] ⋯ output · 03:18:01 → 03:18:03 · 2.5s "
    body = "This is a sentence that should not be split into one character per line when wrapped."
    wrapped = ParallelDisplay._wrap_body_with_hanging_indent(
        prefix=prefix, body=body, total_width=80, body_measure=80
    )
    lines = wrapped.splitlines()
    assert len(lines) <= 20
    assert max(len(line) for line in lines) >= 5


def test_parallel_display_wrap_close_body_wide_chrome_prefix_with_trailer() -> None:
    header = "⋯ output · 05:15:54 → 05:15:56 · 1.7s"
    hang_prefix = "05:15:56 [output][claude-headless/haiku] "
    visible = "This is a sentence that should not be split into one character per line when wrapped. (truncated, 16 lines · 1.7 KiB, see .agent/raw/claude-headless_haiku.log)"
    chrome_width = len(hang_prefix)
    rows = ParallelDisplay._wrap_close_body(
        header,
        visible,
        total_width=80,
        hang_prefix=hang_prefix,
        chrome_prefix_width=chrome_width,
    )
    assert len(rows) <= 15
    assert all(len(r) >= 5 for r in rows)


def test_a_dropped_unit_cannot_write_again_through_a_retained_handle(tmp_path: Path) -> None:
    console, _buf = _make_wide_console()
    pd = ParallelDisplay(make_display_context(console=console, env={}), workspace_root=tmp_path)

    pd._emit_activity_event("unit-dropped", ActivityEventKind.TEXT, "B" * 5000, None, {})
    retained = pd._condensed_logs["unit-dropped"]
    pd.drop_unit("unit-dropped")

    overflow_log = tmp_path / ".agent" / "raw" / "unit-dropped.overflow.log"
    before = overflow_log.read_text(encoding="utf-8")

    assert retained.is_disabled
    assert retained.append("a line written after the drop") is False
    assert overflow_log.read_text(encoding="utf-8") == before
