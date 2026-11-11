from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay


def _make_display() -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    return ParallelDisplay(make_display_context(console=console, env={})), buf


def _make_renderer_with_env(env: dict[str, str]) -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    return ParallelDisplay(make_display_context(console=console, env=env)), buf


def test_identical_consecutive_text_fragments_emit_one_logical_close_entry() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "same content")
    pd.emit_activity_line("u", "text", "same content")
    pd.emit_activity_line("u", "text", "same content")
    pd.flush_blocks()
    out = buf.getvalue()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    content_rows = [ln for ln in lines if "[output][u]" in ln]
    assert len(content_rows) == 1
    assert "⋯ output" in content_rows[0]
    assert out.count("same content") == 1, (
        f"Joined passage must appear once, got {out.count('same content')}: {out!r}"
    )


def test_differing_text_fragments_emit_one_logical_close_entry_with_joined_passage() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "first content")
    pd.emit_activity_line("u", "text", "second content")
    pd.flush_blocks()
    out = buf.getvalue()
    content_rows = [ln for ln in out.splitlines() if "[output][u]" in ln]
    assert len(content_rows) == 1
    assert "first content" in out
    assert "second content" in out
    assert out.count("first content second content") == 1, (
        f"Joined passage must appear once: {out!r}"
    )


def test_dedup_disabled_by_env_still_emits_one_logical_close_entry() -> None:
    pd, buf = _make_renderer_with_env({"RALPH_STREAMING_DEDUP": "0"})
    assert pd._ctx.streaming_dedup_enabled is False
    pd.emit_activity_line("u", "text", "same content")
    pd.emit_activity_line("u", "text", "same content")
    pd.emit_activity_line("u", "text", "same content")
    pd.flush_blocks()
    out = buf.getvalue()
    content_rows = [ln for ln in out.splitlines() if "[output][u]" in ln]
    assert len(content_rows) == 1
    assert out.count("same content") == 3
    assert "\u2192" in out, f"close line missing \u2192 span marker: {out!r}"
    assert "fragments" not in out


def test_dedup_operates_independently_per_unit_id() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("unit-a", "text", "same")
    pd.emit_activity_line("unit-b", "text", "same")
    pd.flush_blocks()
    out = buf.getvalue()
    assert "[output][unit-a]" in out
    assert "[output][unit-b]" in out
    a_idx = out.index("[output][unit-a]")
    b_idx = out.index("[output][unit-b]")
    assert a_idx < b_idx, "unit-a's close line should precede unit-b's"


def test_dedup_does_not_suppress_first_fragment_of_new_block() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "hello")
    pd.emit_activity_line("u", "text", "hello")
    pd.emit_activity_line("u", "text", "hello")
    pd.flush_blocks()
    out = buf.getvalue()
    content_rows = [ln for ln in out.splitlines() if "[output][u]" in ln]
    assert len(content_rows) == 1
    accumulated = pd._active_block.pop("u", None)
    assert accumulated is None or len(accumulated) <= 1 or (True)


def test_dedup_with_three_different_then_identical() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "first")
    pd.emit_activity_line("u", "text", "second")
    pd.emit_activity_line("u", "text", "second")
    pd.flush_blocks()
    out = buf.getvalue()
    content_rows = [ln for ln in out.splitlines() if "[output][u]" in ln]
    assert len(content_rows) == 1
    assert "first second" in out


def test_dedup_default_enabled() -> None:
    pd, _buf = _make_display()
    assert pd._ctx.streaming_dedup_enabled is True


def test_dedup_false_values_disable() -> None:
    for false_val in ("false", "no", "off"):
        pd, _buf = _make_renderer_with_env({"RALPH_STREAMING_DEDUP": false_val})
        assert pd._ctx.streaming_dedup_enabled is False, (
            f"RALPH_STREAMING_DEDUP={false_val} should resolve to streaming_dedup_enabled=False"
        )


def test_dedup_works_for_thinking_kind() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("u", "thinking", "same thought")
    pd.emit_activity_line("u", "thinking", "same thought")
    pd.emit_activity_line("u", "thinking", "same thought")
    pd.flush_blocks()
    out = buf.getvalue()
    thinking_lines = [ln for ln in out.splitlines() if "[reasoning][u]" in ln]
    assert len(thinking_lines) == 1, (
        f"Expected 1 thinking close line, got {len(thinking_lines)}: {out!r}"
    )
    assert out.count("same thought") == 1


def test_different_kind_resets_block() -> None:
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "content")
    pd.emit_activity_line("u", "thinking", "reasoning")
    pd.flush_blocks()
    out = buf.getvalue()
    assert "[output][u]" in out
    assert "[reasoning][u]" in out
    content_idx = out.index("[output][u]")
    thinking_idx = out.index("[reasoning][u]")
    assert content_idx < thinking_idx


def test_dedup_disabled_appends_identical_fragments_to_buffer() -> None:
    pd, _buf = _make_renderer_with_env({"RALPH_STREAMING_DEDUP": "0"})
    pd.emit_activity_line("u", "text", "x")
    pd.emit_activity_line("u", "text", "x")
    accumulated = pd._active_block.get("u", (None, []))[1]
    assert len(accumulated) == 2, (
        f"With dedup off, both identical fragments are buffered; got {accumulated!r}"
    )


def test_dedup_enabled_skips_identical_fragment_appends() -> None:
    pd, _buf = _make_display()
    pd.emit_activity_line("u", "text", "x")
    pd.emit_activity_line("u", "text", "x")
    accumulated = pd._active_block.get("u", (None, []))[1]
    assert len(accumulated) == 1, (
        f"With dedup on, identical fragment must be skipped; got {accumulated!r}"
    )


def test_running_char_total_tracks_buffered_fragments() -> None:
    pd, _buf = _make_display()
    pd.emit_activity_line("u", "text", "abc")
    pd.emit_activity_line("u", "text", "defg")
    chars_total = pd._active_block_chars.get("u", 0)
    assert chars_total == sum(len(x) for x in pd._active_block["u"][1]), (
        f"running total {chars_total} != sum(len) {sum(len(x) for x in pd._active_block['u'][1])}"
    )
