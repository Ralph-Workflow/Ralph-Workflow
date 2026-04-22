"""Tests for PlainLogRenderer identical consecutive streaming fragment suppression."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from rich.console import Console

from ralph.display.plain_renderer import PlainLogRenderer

# Threshold for number of fragments in dedup test
_THREE_FRAGMENTS = 3


def _make_renderer() -> tuple[PlainLogRenderer, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    return PlainLogRenderer(console), buf


def test_identical_consecutive_text_fragments_suppressed() -> None:
    """Three identical text deltas emit one [content-start] and zero [content-continue#N]."""
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "text", "same content")
    renderer.emit_activity_line("u", "text", "same content")
    renderer.emit_activity_line("u", "text", "same content")
    out = buf.getvalue()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    # Only one content-start should appear
    content_start_count = sum(1 for ln in lines if "[content-start]" in ln)
    assert content_start_count == 1, f"Expected 1 [content-start], got {content_start_count}"
    # No content-continue lines should appear for identical content
    content_continue_count = sum(1 for ln in lines if "[content-continue#" in ln)
    assert content_continue_count == 0, (
        f"Expected 0 [content-continue#], got {content_continue_count}"
    )


def test_differing_text_fragments_emit_continue_lines() -> None:
    """Differing text deltas emit [content-start] plus [content-continue#2]."""
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "text", "first content")
    renderer.emit_activity_line("u", "text", "second content")
    out = buf.getvalue()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    content_start_count = sum(1 for ln in lines if "[content-start]" in ln)
    assert content_start_count == 1
    content_continue_count = sum(1 for ln in lines if "[content-continue#" in ln)
    assert content_continue_count == 1, (
        f"Expected 1 [content-continue#], got {content_continue_count}"
    )
    assert "[content-continue#2]" in out


def test_dedup_disabled_by_env_restore_duplicates() -> None:
    """RALPH_STREAMING_DEDUP=0 disables suppression and duplicates are emitted."""
    renderer, buf = _make_renderer()
    with patch.dict("os.environ", {"RALPH_STREAMING_DEDUP": "0"}):
        renderer.emit_activity_line("u", "text", "same content")
        renderer.emit_activity_line("u", "text", "same content")
        renderer.emit_activity_line("u", "text", "same content")
    out = buf.getvalue()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    content_start_count = sum(1 for ln in lines if "[content-start]" in ln)
    assert content_start_count == 1
    content_continue_count = sum(1 for ln in lines if "[content-continue#" in ln)
    expected_count = _THREE_FRAGMENTS - 1
    assert content_continue_count == expected_count, (
        f"Expected {expected_count} [content-continue#] with dedup disabled,"
        f" got {content_continue_count}"
    )


def test_dedup_operates_independently_per_unit_id() -> None:
    """Fragment 'x' in unit A does NOT suppress fragment 'x' in unit B.

    Because of the single-block invariant, unit B opens a NEW block after unit A's block
    closes. So each unit gets its own streaming sequence.
    """
    renderer, buf = _make_renderer()
    # Emit for unit-a
    renderer.emit_activity_line("unit-a", "text", "same")
    # Switch to unit-b (closes unit-a's block, opens new for unit-b)
    renderer.emit_activity_line("unit-b", "text", "same")
    out = buf.getvalue()
    # Both units should have their own [content-start]
    assert "[content-start][unit-a]" in out
    assert "[content-start][unit-b]" in out


def test_dedup_does_not_suppress_first_fragment_of_new_block() -> None:
    """First fragment is always emitted; dedup applies only to subsequent identical ones."""
    renderer, buf = _make_renderer()
    # First block: "hello" opens a new block
    renderer.emit_activity_line("u", "text", "hello")
    # Second identical fragment gets deduplicated
    renderer.emit_activity_line("u", "text", "hello")
    # Third identical fragment also deduplicated
    renderer.emit_activity_line("u", "text", "hello")
    out = buf.getvalue()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    # Only one [content-start] should appear
    content_start_count = sum(1 for ln in lines if "[content-start]" in ln)
    assert content_start_count == 1, f"Expected 1 [content-start], got {content_start_count}"


def test_dedup_with_three_different_then_identical() -> None:
    """After two different fragments, a third identical to the second is deduplicated."""
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "text", "first")
    renderer.emit_activity_line("u", "text", "second")
    # Same as second — should be deduplicated
    renderer.emit_activity_line("u", "text", "second")
    out = buf.getvalue()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    content_continue_count = sum(1 for ln in lines if "[content-continue#" in ln)
    # Only one continue line for the second fragment (seq=2)
    assert content_continue_count == 1, (
        f"Expected 1 [content-continue#], got {content_continue_count}"
    )


def test_dedup_default_enabled() -> None:
    """By default (no env var), dedup is enabled."""
    renderer, buf = _make_renderer()
    # Clear any env var that might interfere
    with patch.dict("os.environ", {"RALPH_STREAMING_DEDUP": ""}):
        renderer.emit_activity_line("u", "text", "x")
        renderer.emit_activity_line("u", "text", "x")
    out = buf.getvalue()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    content_continue_count = sum(1 for ln in lines if "[content-continue#" in ln)
    assert content_continue_count == 0, "Dedup should be enabled by default"


def test_dedup_false_values_disable() -> None:
    """Various false values (false, no, off) all disable dedup."""
    for false_val in ("false", "no", "off"):
        renderer, buf = _make_renderer()
        with patch.dict("os.environ", {"RALPH_STREAMING_DEDUP": false_val}):
            renderer.emit_activity_line("u", "text", "x")
            renderer.emit_activity_line("u", "text", "x")
        out = buf.getvalue()
        content_continue_count = sum(1 for ln in out.splitlines() if "[content-continue#" in ln)
        assert content_continue_count == 1, (
            f"RALPH_STREAMING_DEDUP={false_val} should disable dedup"
        )


def test_dedup_works_for_thinking_kind() -> None:
    """Identical consecutive thinking fragments are also deduplicated."""
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "thinking", "same thought")
    renderer.emit_activity_line("u", "thinking", "same thought")
    renderer.emit_activity_line("u", "thinking", "same thought")
    out = buf.getvalue()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    thinking_start_count = sum(1 for ln in lines if "[thinking-start]" in ln)
    assert thinking_start_count == 1, f"Expected 1 [thinking-start], got {thinking_start_count}"
    thinking_continue_count = sum(1 for ln in lines if "[thinking-continue#" in ln)
    assert thinking_continue_count == 0, (
        f"Expected 0 [thinking-continue#], got {thinking_continue_count}"
    )


def test_different_kind_resets_dedup() -> None:
    """Switching from text to thinking (different kind) resets the dedup state."""
    renderer, buf = _make_renderer()
    renderer.emit_activity_line("u", "text", "content")
    renderer.emit_activity_line("u", "thinking", "same content")  # Different kind = new block
    out = buf.getvalue()
    assert "[thinking-start]" in out
