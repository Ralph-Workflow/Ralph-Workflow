"""Unit tests for the content_condenser utility."""

from __future__ import annotations

from rich.cells import cell_len

from ralph.display.content_condenser import condense_content

_SOFT_LIMIT = 400
_HARD_LIMIT = 4000
_LONG_TEXT_LEN = 5000


def test_short_text_passes_through() -> None:
    text = "hello world"
    visible, condensed = condense_content(text)
    assert visible == text
    assert condensed is False


def test_empty_string_returns_false() -> None:
    visible, condensed = condense_content("")
    assert visible == ""
    assert condensed is False


def test_none_equivalent_empty() -> None:
    visible, condensed = condense_content("", soft_limit=10)
    assert visible == ""
    assert condensed is False


def test_soft_limit_head_truncation() -> None:
    text = "a" * (_SOFT_LIMIT + 100)
    visible, condensed = condense_content(text, soft_limit=_SOFT_LIMIT, hard_limit=_HARD_LIMIT)
    assert condensed is True
    assert "…" in visible
    assert len(visible) < len(text)


def test_soft_limit_includes_overflow_ref() -> None:
    text = "x" * (_SOFT_LIMIT + 100)
    visible, condensed = condense_content(
        text, soft_limit=_SOFT_LIMIT, hard_limit=_HARD_LIMIT, overflow_ref=".agent/raw/u.log"
    )
    assert condensed is True
    assert ".agent/raw/u.log" in visible


def test_hard_limit_head_tail_condensation() -> None:
    text = "a" * 2000 + "MIDDLE_MARKER" + "b" * 2000
    visible, condensed = condense_content(text, soft_limit=_SOFT_LIMIT, hard_limit=500)
    assert condensed is True
    assert "a" in visible
    assert "b" in visible
    assert "MIDDLE_MARKER" not in visible


def test_hard_limit_shows_chars_count_with_ref() -> None:
    text = "a" * _LONG_TEXT_LEN
    visible, condensed = condense_content(
        text, soft_limit=_SOFT_LIMIT, hard_limit=1000, overflow_ref=".agent/raw/u.log"
    )
    assert condensed is True
    assert "chars" in visible
    assert ".agent/raw/u.log" in visible


def test_emoji_boundary_safety() -> None:
    # Each emoji is 2 cells wide so 300 emojis = 600 cells, exceeds soft_limit=400
    emoji_text = "😀" * 300
    visible, condensed = condense_content(
        emoji_text, soft_limit=_SOFT_LIMIT, hard_limit=_HARD_LIMIT
    )
    assert condensed is True
    # The visible head should only contain whole emojis — no partial multi-byte sequences
    emoji_chars = [c for c in visible if c == "😀"]
    assert len(emoji_chars) > 0
    # Verify cell count of emojis fits within soft_limit
    assert cell_len("".join(emoji_chars)) <= _SOFT_LIMIT


def test_overflow_ref_none_uses_fallback() -> None:
    text = "a" * (_SOFT_LIMIT + 100)
    visible, condensed = condense_content(text, soft_limit=_SOFT_LIMIT, overflow_ref=None)
    assert condensed is True
    assert "raw unavailable" in visible


def test_exactly_at_soft_limit_passthrough() -> None:
    text = "a" * _SOFT_LIMIT
    visible, condensed = condense_content(text, soft_limit=_SOFT_LIMIT)
    assert visible == text
    assert condensed is False
