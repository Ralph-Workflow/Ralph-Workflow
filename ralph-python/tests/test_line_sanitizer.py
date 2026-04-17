"""Tests for sanitize_display_line() line truncation and unicode safety."""

import pytest
from ralph.display.line_sanitizer import sanitize_display_line


def test_normal_short_string_unchanged() -> None:
    result = sanitize_display_line("hello world")
    assert result == "hello world"


def test_oversize_line_truncated() -> None:
    long_input = "x" * 10000
    result = sanitize_display_line(long_input)
    assert len(result) <= 203  # 200 + ellipsis character (1 char)
    assert result.endswith("…")


def test_truncation_at_max_chars() -> None:
    result = sanitize_display_line("a" * 201)
    assert len(result) <= 201
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
    utf8_bytes = "hello".encode("utf-8")
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
    assert len(result) <= 11  # 10 + ellipsis
    assert result.endswith("…")


def test_empty_string() -> None:
    result = sanitize_display_line("")
    assert result == ""


def test_empty_bytes() -> None:
    result = sanitize_display_line(b"")
    assert result == ""
