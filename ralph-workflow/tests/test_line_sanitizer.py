from ralph.display.line_sanitizer import (
    sanitize_display_line,
    strip_markup_safe,
    strip_terminal_control,
)

MAX_TRUNCATED_RESULT_LENGTH = 203
MAX_CHARS_WITH_ELLIPSIS = 201
CUSTOM_MAX_RESULT_LENGTH = 11

HOSTILE_LINE = "\x1b[?1049h\x1b[2J\x1b[>0cboom"

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
    assert "hello" in result or len(result) >= 0


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
    assert "\x07" not in result
    assert "\t" in result


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


def test_sanitize_display_line_drops_sgr_color_body_completely() -> None:
    result = sanitize_display_line("\x1b[31mx")
    assert result == "x", (
        f"sanitize_display_line must strip the SGR body, leaving 'x' alone; got {result!r}"
    )


def test_strip_terminal_control_alternate_screen_enter_leave() -> None:
    assert strip_terminal_control("\x1b[?1049h") == ""
    assert strip_terminal_control("\x1b[?1049l") == ""


def test_strip_terminal_control_erase_display_and_line() -> None:
    assert strip_terminal_control("\x1b[2J") == ""
    assert strip_terminal_control("\x1b[K") == ""


def test_strip_terminal_control_cursor_position() -> None:
    assert strip_terminal_control("\x1b[H") == ""
    assert strip_terminal_control("\x1b[12;40H") == ""


def test_strip_terminal_control_scroll_region() -> None:
    assert strip_terminal_control("\x1b[1;50r") == ""


def test_strip_terminal_control_cursor_hide_show() -> None:
    assert strip_terminal_control("\x1b[?25l") == ""
    assert strip_terminal_control("\x1b[?25h") == ""


def test_strip_terminal_control_private_parameter_gt() -> None:
    assert strip_terminal_control("A\x1b[>0cB") == "AB"


def test_strip_terminal_control_private_parameter_lt() -> None:
    assert strip_terminal_control("\x1b[<35;1;2M") == ""


def test_strip_terminal_control_sgr_color() -> None:
    assert strip_terminal_control("\x1b[32mhi\x1b[0m") == "hi"


def test_strip_terminal_control_osc_title_bel_terminated() -> None:
    assert strip_terminal_control("\x1b]0;some title\x07") == ""


def test_strip_terminal_control_osc_title_st_terminated() -> None:
    assert strip_terminal_control("\x1b]0;t\x1b\\") == ""


def test_strip_terminal_control_string_terminated_and_dangling_sequences() -> None:
    assert strip_terminal_control("\x1bPtmux;payload\x1b\\") == ""
    assert strip_terminal_control("\x1b_Ga=T,payload\x1b\\") == ""
    assert strip_terminal_control("before\x1b]0;title") == "before"


def test_strip_terminal_control_two_char_esc() -> None:
    assert strip_terminal_control("\x1bM") == ""


def test_strip_terminal_control_c0_controls() -> None:
    assert strip_terminal_control("\x07bell") == "bell"
    assert strip_terminal_control("\x1funit") == "unit"
    assert strip_terminal_control("a\x7fb") == "ab"


def test_strip_terminal_control_visible_text_survives_hostile_line() -> None:
    assert strip_terminal_control(HOSTILE_LINE) == "boom"


def test_strip_terminal_control_keeps_tab() -> None:
    assert strip_terminal_control("a\tb") == "a\tb"


def test_strip_terminal_control_keeps_newline() -> None:
    assert strip_terminal_control("line1\nline2") == "line1\nline2"


def test_strip_terminal_control_keeps_color_payload_with_surrounding_text() -> None:
    text = "before\x1b[31mCOLORED\x1b[0mafter"
    assert strip_terminal_control(text) == "beforeCOLOREDafter"


def test_strip_markup_safe_preserves_unclosed_bracket_markdown_link() -> None:
    text = "Full implementation at [todo-list.js](file:///workspace/todo-list.js)"
    assert strip_markup_safe(text) == text


def test_strip_markup_safe_preserves_bracketed_tool_name() -> None:
    text = "ran [write_to_file] against todo-list.js"
    assert strip_markup_safe(text) == text


def test_strip_markup_safe_falls_back_to_literal_on_unmatched_closing_tag() -> None:
    text = "grep pattern [/pdf /text /imageb] matched"
    assert strip_markup_safe(text) == text


def test_strip_markup_safe_reduces_genuinely_matched_markup_pair() -> None:
    assert strip_markup_safe("[bold]hello[/bold]") == "hello"


def test_strip_markup_safe_strips_terminal_control_around_bracket_text() -> None:
    text = "\x1b[31m[todo-list.js]\x1b[0m(file:///a)"
    assert strip_markup_safe(text) == "[todo-list.js](file:///a)"
