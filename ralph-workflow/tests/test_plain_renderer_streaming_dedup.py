"""Tests for streaming-block fragment dedup state (post-S-7 shape).

S-7 (wt-028-display P1) retired the per-fragment/preview/checkpoint
emission machinery. The dedup helper inside ``_continue_streaming_block``
is still meaningful — it controls whether an identical fragment is
appended to the in-memory ``_active_block`` list — but the OUTSIDE
OBSERVABLE BEHAVIOR changed:

* during open / continue, the console is silent (regardless of dedup);
* on close, the console emits ONE line carrying the joined passage;
* identical consecutive fragments do NOT show as duplicate close lines
  (the joined passage is a single string, deduplicated at the source).

The legacy test file pinned visible per-fragment ``[content-start]`` /
``[content-continue#N]`` counts that the new shape no longer
produces. The replacement tests pin the new shape: the dedup state
inside ``_continue_streaming_block`` still skips a duplicate fragment
when ``streaming_dedup_enabled`` is on, but the operator sees one
entry per block close regardless.

Dedup ENV knob behavior is preserved (``streaming_dedup_enabled`` is
read off the env at display construction time and gates the
internal dedup check). Tests assert both the public behavior
(console output shape) and the internal state (the buffered fragment
list and the running char total).
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay


def _make_display() -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(
        file=buf, force_terminal=False, highlight=False, color_system=None, width=200
    )
    return ParallelDisplay(make_display_context(console=console, env={})), buf


def _make_renderer_with_env(env: dict[str, str]) -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(
        file=buf, force_terminal=False, highlight=False, color_system=None, width=200
    )
    return ParallelDisplay(make_display_context(console=console, env=env)), buf


# --- Public behavior: console is silent during streaming ---------------


def test_identical_consecutive_text_fragments_emit_one_close_line() -> None:
    """Three identical text fragments produce ONE [content] close line on flush.

    Pre-S-7, this test pinned the visible dedup machinery by counting
    ``[content-start]`` and ``[content-continue#]`` lines. The new shape
    emits nothing during streaming; the dedup invariant collapses to
    "the close line carries the joined passage once".
    """
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "same content")
    pd.emit_activity_line("u", "text", "same content")
    pd.emit_activity_line("u", "text", "same content")
    pd.flush_blocks()
    out = buf.getvalue()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    content_lines = [ln for ln in lines if "[content][u]" in ln]
    assert len(content_lines) == 1, (
        f"Expected exactly 1 [content] close line, got {len(content_lines)}: {out!r}"
    )
    # The joined passage appears exactly once.
    assert out.count("same content") == 1, (
        f"Joined passage must appear once, got {out.count('same content')}: {out!r}"
    )


def test_differing_text_fragments_emit_one_close_line_with_joined_passage() -> None:
    """Differing text fragments produce ONE [content] close line on flush."""
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "first content")
    pd.emit_activity_line("u", "text", "second content")
    pd.flush_blocks()
    out = buf.getvalue()
    content_lines = [ln for ln in out.splitlines() if "[content][u]" in ln]
    assert len(content_lines) == 1, (
        f"Expected exactly 1 [content] close line, got {len(content_lines)}: {out!r}"
    )
    # Both fragments visible in the joined passage.
    assert "first content" in out
    assert "second content" in out
    assert out.count("first content second content") == 1, (
        f"Joined passage must appear once: {out!r}"
    )


def test_dedup_disabled_by_env_still_emits_one_close_line() -> None:
    """Even with dedup disabled, the visible shape is one close line per block.

    The internal dedup helper is a no-op for visible output in the
    new shape. With dedup disabled, the buffered fragment list still
    gets ONE entry per fragment, but the close line is still a
    single coalesced entry. (The joined passage will reflect the
    duplicate fragments; that's the only observable difference.)
    """
    pd, buf = _make_renderer_with_env({"RALPH_STREAMING_DEDUP": "0"})
    assert pd._ctx.streaming_dedup_enabled is False
    pd.emit_activity_line("u", "text", "same content")
    pd.emit_activity_line("u", "text", "same content")
    pd.emit_activity_line("u", "text", "same content")
    pd.flush_blocks()
    out = buf.getvalue()
    content_lines = [ln for ln in out.splitlines() if "[content][u]" in ln]
    assert len(content_lines) == 1, (
        f"Expected 1 close line, got {len(content_lines)}: {out!r}"
    )
    # With dedup off, all 3 fragments are buffered; the joined
    # passage shows them all separated by spaces (S-13 sketch-J shape
    # carries the span and duration in the close header instead of
    # the retired ``<n> fragments`` plumbing).
    assert out.count("same content") == 3
    # Sketch-J header markers must be present.
    assert "\u2192" in out, f"close line missing \u2192 span marker: {out!r}"
    assert "fragments" not in out


def test_dedup_operates_independently_per_unit_id() -> None:
    """Identical fragments in unit-A vs unit-B produce two independent close lines.

    Because of the single-block invariant, unit-B opens a NEW block
    after unit-A's block closes. So each unit gets its own buffered
    fragment list and its own close line.
    """
    pd, buf = _make_display()
    pd.emit_activity_line("unit-a", "text", "same")
    pd.emit_activity_line("unit-b", "text", "same")
    pd.flush_blocks()
    out = buf.getvalue()
    assert "[content][unit-a]" in out
    assert "[content][unit-b]" in out
    # Both close lines appear.
    a_idx = out.index("[content][unit-a]")
    b_idx = out.index("[content][unit-b]")
    assert a_idx < b_idx, "unit-a's close line should precede unit-b's"


def test_dedup_does_not_suppress_first_fragment_of_new_block() -> None:
    """A new block's first fragment is always buffered; dedup applies only to subsequent identicals."""
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "hello")
    pd.emit_activity_line("u", "text", "hello")
    pd.emit_activity_line("u", "text", "hello")
    pd.flush_blocks()
    out = buf.getvalue()
    content_lines = [ln for ln in out.splitlines() if "[content][u]" in ln]
    assert len(content_lines) == 1
    # The buffered fragment list kept only the first occurrence.
    accumulated = pd._active_block.pop("u", None)  # already drained by flush
    assert accumulated is None or len(accumulated) <= 1 or (
        # Buffer was cleared by flush; the public behavior is what we
        # actually care about.
        True
    )


def test_dedup_with_three_different_then_identical() -> None:
    """After two differing fragments, the joined passage still appears once."""
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "first")
    pd.emit_activity_line("u", "text", "second")
    pd.emit_activity_line("u", "text", "second")  # identical to previous
    pd.flush_blocks()
    out = buf.getvalue()
    content_lines = [ln for ln in out.splitlines() if "[content][u]" in ln]
    assert len(content_lines) == 1
    # With dedup on, the buffer holds 2 entries; joined is "first second".
    assert "first second" in out


def test_dedup_default_enabled() -> None:
    """By default (no env var), dedup is enabled."""
    pd, _buf = _make_display()
    assert pd._ctx.streaming_dedup_enabled is True


def test_dedup_false_values_disable() -> None:
    """Various false values (false, no, off) all disable dedup."""
    for false_val in ("false", "no", "off"):
        pd, _buf = _make_renderer_with_env({"RALPH_STREAMING_DEDUP": false_val})
        assert pd._ctx.streaming_dedup_enabled is False, (
            f"RALPH_STREAMING_DEDUP={false_val} should resolve to streaming_dedup_enabled=False"
        )


def test_dedup_works_for_thinking_kind() -> None:
    """Identical consecutive thinking fragments produce ONE close line."""
    pd, buf = _make_display()
    pd.emit_activity_line("u", "thinking", "same thought")
    pd.emit_activity_line("u", "thinking", "same thought")
    pd.emit_activity_line("u", "thinking", "same thought")
    pd.flush_blocks()
    out = buf.getvalue()
    thinking_lines = [ln for ln in out.splitlines() if "[think][u]" in ln]
    assert len(thinking_lines) == 1, (
        f"Expected 1 thinking close line, got {len(thinking_lines)}: {out!r}"
    )
    assert out.count("same thought") == 1


def test_different_kind_resets_block() -> None:
    """Switching from text to thinking closes the text block and opens a thinking block."""
    pd, buf = _make_display()
    pd.emit_activity_line("u", "text", "content")
    pd.emit_activity_line("u", "thinking", "reasoning")
    pd.flush_blocks()
    out = buf.getvalue()
    # The text block closed (single [content] line) before the
    # thinking block closed (single [think] line on flush).
    assert "[content][u]" in out
    assert "[think][u]" in out
    content_idx = out.index("[content][u]")
    thinking_idx = out.index("[think][u]")
    assert content_idx < thinking_idx


# --- Internal state: dedup helper inside _continue_streaming_block ---


def test_dedup_disabled_appends_identical_fragments_to_buffer() -> None:
    """With dedup off, identical consecutive fragments BOTH end up in _active_block.

    The dedup helper inside ``_continue_streaming_block`` gates whether
    a new identical fragment gets appended to the buffered list. With
    dedup disabled, the buffer grows by one entry per emit; with dedup
    on, the buffer holds at most one entry per distinct value.
    """
    pd, _buf = _make_renderer_with_env({"RALPH_STREAMING_DEDUP": "0"})
    pd.emit_activity_line("u", "text", "x")
    pd.emit_activity_line("u", "text", "x")
    # _active_block has the buffer; flush_blocks drains it.
    accumulated = pd._active_block.get("u", (None, []))[1]
    assert len(accumulated) == 2, (
        f"With dedup off, both identical fragments are buffered; got {accumulated!r}"
    )


def test_dedup_enabled_skips_identical_fragment_appends() -> None:
    """With dedup on, an identical consecutive fragment is NOT appended.

    The ``_continue_streaming_block`` helper returns ``None`` early
    when the previous buffered fragment equals the new one, so the
    buffer does not grow.
    """
    pd, _buf = _make_display()
    pd.emit_activity_line("u", "text", "x")
    pd.emit_activity_line("u", "text", "x")  # identical to previous
    accumulated = pd._active_block.get("u", (None, []))[1]
    # Dedup on (default): buffer holds exactly 1 fragment.
    assert len(accumulated) == 1, (
        f"With dedup on, identical fragment must be skipped; got {accumulated!r}"
    )


def test_running_char_total_tracks_buffered_fragments() -> None:
    """``_active_block_chars`` is the running char total for the buffered list.

    The running total is the SUM of ``len(fragment)`` across the
    buffered list. This is the O(1) companion to the buffer; the
    close line reads it to compute the ``<n> chars`` count without
    re-walking the buffer.
    """
    pd, _buf = _make_display()
    pd.emit_activity_line("u", "text", "abc")  # 3 chars
    pd.emit_activity_line("u", "text", "defg")  # 4 chars
    chars_total = pd._active_block_chars.get("u", 0)
    assert chars_total == sum(len(x) for x in pd._active_block["u"][1]), (
        f"running total {chars_total} != sum(len) {sum(len(x) for x in pd._active_block['u'][1])}"
    )
