from __future__ import annotations

from rich.cells import cell_len

from ralph.display.content_condenser import CondenseOptions, condense_content

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
    visible, condensed = condense_content("", options=CondenseOptions(soft_limit=10))
    assert visible == ""
    assert condensed is False


def test_soft_limit_head_truncation() -> None:
    text = "a" * (_SOFT_LIMIT + 100)
    visible, condensed = condense_content(
        text, options=CondenseOptions(soft_limit=_SOFT_LIMIT, hard_limit=_HARD_LIMIT)
    )
    assert condensed is True
    assert "…" in visible
    assert len(visible) < len(text)


def test_soft_limit_includes_overflow_ref() -> None:
    text = "x" * (_SOFT_LIMIT + 100)
    visible, condensed = condense_content(
        text,
        options=CondenseOptions(
            soft_limit=_SOFT_LIMIT, hard_limit=_HARD_LIMIT, overflow_ref=".agent/raw/u.log"
        ),
    )
    assert condensed is True
    assert ".agent/raw/u.log" in visible


def test_hard_limit_head_tail_condensation() -> None:
    text = "a" * 2000 + "MIDDLE_MARKER" + "b" * 2000
    visible, condensed = condense_content(
        text, options=CondenseOptions(soft_limit=_SOFT_LIMIT, hard_limit=500)
    )
    assert condensed is True
    assert "a" in visible
    assert "b" in visible
    assert "MIDDLE_MARKER" not in visible


def test_hard_limit_shows_chars_count_with_ref() -> None:
    text = "a" * _LONG_TEXT_LEN
    visible, condensed = condense_content(
        text,
        options=CondenseOptions(
            soft_limit=_SOFT_LIMIT, hard_limit=1000, overflow_ref=".agent/raw/u.log"
        ),
    )
    assert condensed is True
    assert "chars" in visible
    assert ".agent/raw/u.log" in visible


def test_emoji_boundary_safety() -> None:
    emoji_text = "😀" * 300
    visible, condensed = condense_content(
        emoji_text, options=CondenseOptions(soft_limit=_SOFT_LIMIT, hard_limit=_HARD_LIMIT)
    )
    assert condensed is True
    emoji_chars = [c for c in visible if c == "😀"]
    assert len(emoji_chars) > 0
    assert cell_len("".join(emoji_chars)) <= _SOFT_LIMIT


def test_overflow_ref_none_produces_truncated_without_path() -> None:
    text = "a" * (_SOFT_LIMIT + 100)
    visible, condensed = condense_content(
        text, options=CondenseOptions(soft_limit=_SOFT_LIMIT, overflow_ref=None)
    )
    assert condensed is True
    assert "(truncated, 1 line · 500 B)" in visible
    assert "raw unavailable" not in visible


def test_exactly_at_soft_limit_passthrough() -> None:
    text = "a" * _SOFT_LIMIT
    visible, condensed = condense_content(text, options=CondenseOptions(soft_limit=_SOFT_LIMIT))
    assert visible == text
    assert condensed is False


def test_soft_limit_marker_carries_count_size_and_destination() -> None:
    text = "x" * (_SOFT_LIMIT + 200)
    visible, _ = condense_content(
        text,
        options=CondenseOptions(
            soft_limit=_SOFT_LIMIT,
            hard_limit=_HARD_LIMIT,
            overflow_ref=".agent/raw/u.log",
        ),
    )
    assert "1 line" in visible
    assert "600 B" in visible
    assert ".agent/raw/u.log" in visible


def test_head_only_marker_reports_actual_hidden_line_count() -> None:
    text = "\n".join(f"line {index:02d}" for index in range(60))
    visible, condensed = condense_content(
        text,
        options=CondenseOptions(
            soft_limit=100,
            hard_limit=4_000,
            overflow_ref=".agent/raw/u.log",
        ),
    )
    assert condensed is True
    assert "45 lines" in visible
    assert "1 line" not in visible


def test_hard_limit_marker_carries_count_size_and_destination() -> None:
    text = "a" * _LONG_TEXT_LEN
    visible, _ = condense_content(
        text,
        options=CondenseOptions(
            soft_limit=_SOFT_LIMIT,
            hard_limit=1000,
            overflow_ref=".agent/raw/u.log",
        ),
    )
    assert "1 line" in visible
    assert "KiB" in visible
    assert "chars elided" in visible
    assert ".agent/raw/u.log" in visible


def test_marker_reports_utf8_bytes_not_display_cells() -> None:
    text = "é" * 500
    visible, condensed = condense_content(
        text,
        options=CondenseOptions(soft_limit=_SOFT_LIMIT, hard_limit=_HARD_LIMIT),
    )
    assert condensed is True
    assert "1000 B" in visible


def test_marker_kib_format_above_one_kib() -> None:
    text = "a" * (1024 * 4)
    visible, _ = condense_content(
        text,
        options=CondenseOptions(
            soft_limit=_SOFT_LIMIT,
            hard_limit=2000,
            overflow_ref=".agent/raw/u.log",
        ),
    )
    assert "KiB" in visible
    assert ".agent/raw/u.log" in visible
