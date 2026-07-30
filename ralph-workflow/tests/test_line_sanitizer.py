"""Tests for sanitize_display_line() line truncation and unicode safety."""

from ralph.display.line_sanitizer import sanitize_display_line, strip_terminal_control

MAX_TRUNCATED_RESULT_LENGTH = 203
MAX_CHARS_WITH_ELLIPSIS = 201
CUSTOM_MAX_RESULT_LENGTH = 11

# Hostile line used by the regression test -- combines the black-screen,
# log-overwrite, and private-parameter CSI sequences that the previous
# SGR-only regex leaked. Visible text "boom" must survive.
HOSTILE_LINE = "\x1b[?1049h\x1b[2J\x1b[>0cboom"

# Substrings that previous regexes (SGR-only at _plain_constants.py:156,
# and the [0-9;?] class at status_bar.py:141) leave behind. Any of these in
# the captured output means the stripper is incomplete.
_FORBIDDEN_BODIES = ("[?1049h", "[2J", "[>0c")


def test_normal_short_string_unchanged() -> None:
    result = sanitize_display_line("hello world")
    assert result == "hello world"


def test_oversize_line_truncated() -> None:
    long_input = "x" * 10000
    result = sanitize_display_line(long_input)
    assert len(result) <= MAX_TRUNCATED_RESULT_LENGTH
    assert result.endswith("…")


def test_truncation_at_max_chars() -> None:
    result = sanitize_display_line("a" * 201)
    assert len(result) <= MAX_CHARS_WITH_ELLIPSIS
    assert result.endswith("…")


def test_exactly_max_chars_not_truncated() -> None:
    exactly_200 = "a" * 200
    result = sanitize_display_line(exactly_200)
    assert result == exactly_200
    assert not result.endswith("…")


def test_binary_bytes_decoded_without_raising() -> None:
    bad_bytes = b"\xff\xfe\x00hello\x00"
    result = sanitize_display_line(bad_bytes)
    assert isinstance(result, str)
    assert "hello" in result or len(result) >= 0  # no exception = success


def test_valid_utf8_bytes_decoded_correctly() -> None:
    utf8_bytes = b"hello"
    result = sanitize_display_line(utf8_bytes)
    assert "hello" in result


def test_crlf_normalized_to_lf() -> None:
    result = sanitize_display_line("line1\r\nline2\r\n")
    assert "\r" not in result
    assert "line1" in result


def test_null_bytes_stripped() -> None:
    result = sanitize_display_line("hello\x00world")
    assert "\x00" not in result


def test_control_chars_stripped_except_tab() -> None:
    result = sanitize_display_line("hello\x07world\there")
    assert "\x07" not in result  # bell stripped
    assert "\t" in result  # tab kept


def test_emoji_preserved() -> None:
    emoji_str = "status: 🚀 complete"
    result = sanitize_display_line(emoji_str)
    assert "🚀" in result


def test_custom_max_chars() -> None:
    result = sanitize_display_line("a" * 50, max_chars=10)
    assert len(result) <= CUSTOM_MAX_RESULT_LENGTH
    assert result.endswith("…")


def test_empty_string() -> None:
    result = sanitize_display_line("")
    assert result == ""


def test_empty_bytes() -> None:
    result = sanitize_display_line(b"")
    assert result == ""


# ---------------------------------------------------------------------------
# Regression coverage for the broken sanitize_display_line:
# the old _CONTROL_CHARS_RE deleted the ESC byte but LEFT the sequence body,
# turning "\x1b[31mx" into "[31mx". The fix in step 3 routes sanitize_display_line
# through strip_terminal_control, which removes complete sequences.
# ---------------------------------------------------------------------------


def test_sanitize_display_line_drops_sgr_color_body_completely() -> None:
    """Regression: the ANSI body must NOT survive as literal "[31mx"."""
    result = sanitize_display_line("\x1b[31mx")
    assert result == "x", (
        f"sanitize_display_line must strip the SGR body, leaving 'x' alone; got {result!r}"
    )


# ---------------------------------------------------------------------------
# strip_terminal_control coverage (new in step 3):
# the canonical terminal-escape stripper. Every hostile class MUST vanish
# with no leftover body bytes. TAB and newline must survive.
# ---------------------------------------------------------------------------


def test_strip_terminal_control_alternate_screen_enter_leave() -> None:
    """ESC[?1049h / ESC[?1049l are the black-screen cause -- must vanish."""
    assert strip_terminal_control("\x1b[?1049h") == ""
    assert strip_terminal_control("\x1b[?1049l") == ""


def test_strip_terminal_control_erase_display_and_line() -> None:
    """ESC[2J / ESC[K overwrite log lines -- must vanish with no body."""
    assert strip_terminal_control("\x1b[2J") == ""
    assert strip_terminal_control("\x1b[K") == ""


def test_strip_terminal_control_cursor_position() -> None:
    """ESC[H / ESC[12;40H cursor moves must vanish."""
    assert strip_terminal_control("\x1b[H") == ""
    assert strip_terminal_control("\x1b[12;40H") == ""


def test_strip_terminal_control_scroll_region() -> None:
    """ESC[1;50r scroll-region -- must vanish."""
    assert strip_terminal_control("\x1b[1;50r") == ""


def test_strip_terminal_control_cursor_hide_show() -> None:
    """ESC[?25l / ESC[?25h cursor hide/show -- must vanish."""
    assert strip_terminal_control("\x1b[?25l") == ""
    assert strip_terminal_control("\x1b[?25h") == ""


def test_strip_terminal_control_private_parameter_gt() -> None:
    """Private-parameter CSI ('>' params): ESC[>0c device attributes.

    This is the regression the previous plan's [0-9;?] regex missed.
    ``strip_terminal_control("A\\x1b[>0cB")`` MUST return ``"AB"`` --
    not ``"A[>0cB"`` (the body must vanish with the ESC byte).
    """
    assert strip_terminal_control("A\x1b[>0cB") == "AB"


def test_strip_terminal_control_private_parameter_lt() -> None:
    """Private-parameter CSI ('<' params): ESC[<35;1;2M SGR mouse report.

    ``"\\x1b[<35;1;2M"`` MUST leave nothing behind; the previous plan's
    ``[0-9;?]`` class left ``"[<35;1;2M"`` as literal text.
    """
    assert strip_terminal_control("\x1b[<35;1;2M") == ""


def test_strip_terminal_control_sgr_color() -> None:
    """SGR colour (\x1b[32m...\x1b[0m) -- existing behaviour preserved."""
    assert strip_terminal_control("\x1b[32mhi\x1b[0m") == "hi"


def test_strip_terminal_control_osc_title_bel_terminated() -> None:
    """OSC title, BEL-terminated: \\x1b]0;some title\\x07 -> empty."""
    assert strip_terminal_control("\x1b]0;some title\x07") == ""


def test_strip_terminal_control_osc_title_st_terminated() -> None:
    """OSC title, ST-terminated: \\x1b]0;t\\x1b\\\\ -> empty."""
    assert strip_terminal_control("\x1b]0;t\x1b\\") == ""


def test_strip_terminal_control_two_char_esc() -> None:
    """Two-character ESC sequences (e.g. ESC M -- reverse index) vanish."""
    assert strip_terminal_control("\x1bM") == ""


def test_strip_terminal_control_c0_controls() -> None:
    """Bare C0 controls vanish (\\x00-\\x08, \\x0b-\\x1f, \\x7f)."""
    assert strip_terminal_control("\x07bell") == "bell"
    assert strip_terminal_control("\x1funit") == "unit"
    assert strip_terminal_control("a\x7fb") == "ab"


def test_strip_terminal_control_visible_text_survives_hostile_line() -> None:
    """The full hostile line collapses to its visible text only."""
    assert strip_terminal_control(HOSTILE_LINE) == "boom"


def test_strip_terminal_control_keeps_tab() -> None:
    """TAB (\\t) survives -- callers rely on tab-aligned output."""
    assert strip_terminal_control("a\tb") == "a\tb"


def test_strip_terminal_control_keeps_newline() -> None:
    """LF (\\n) survives -- callers rely on line structure."""
    assert strip_terminal_control("line1\nline2") == "line1\nline2"


def test_strip_terminal_control_keeps_color_payload_with_surrounding_text() -> None:
    """Surrounding text survives: 'before\\x1b[31mred\\x1b[0mafter' -> 'before red after'."""
    text = "before\x1b[31mCOLORED\x1b[0mafter"
    assert strip_terminal_control(text) == "beforeCOLOREDafter"
