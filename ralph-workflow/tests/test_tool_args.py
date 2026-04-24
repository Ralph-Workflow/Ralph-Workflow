"""Tests for format_tool_input helper."""

from __future__ import annotations

from ralph.display.tool_args import format_tool_input


def test_format_tool_input_orders_known_keys_first() -> None:
    result = format_tool_input({"command": "ls -la", "path": "/tmp", "extra": "x"})
    # path before command before extra (alphabetical)
    assert result.startswith("(path=")
    assert "command=" in result
    assert result.index("path=") < result.index("command=")
    assert result.index("command=") < result.index("extra=")


def test_format_tool_input_truncates_long_values() -> None:
    long_val = "a" * 200
    result = format_tool_input({"path": long_val})
    assert "…" in result
    # Truncated at max_value_chars=120 + ellipsis
    assert len(result) < 160  # noqa: PLR2004


def test_format_tool_input_handles_non_dict_as_empty_string() -> None:
    assert format_tool_input(None) == ""
    assert format_tool_input("string") == ""
    assert format_tool_input(42) == ""
    assert format_tool_input([]) == ""


def test_format_tool_input_strips_newlines() -> None:
    result = format_tool_input({"command": "echo\nhello\nworld"})
    assert "\n" not in result
    assert "echo hello world" in result


def test_format_tool_input_empty_dict_returns_empty_string() -> None:
    assert format_tool_input({}) == ""


def test_format_tool_input_surrounding_parens() -> None:
    result = format_tool_input({"path": "x.py"})
    assert result.startswith("(")
    assert result.endswith(")")


def test_format_tool_input_known_key_order_all_four() -> None:
    result = format_tool_input(
        {"workdir": "/repo", "pattern": "*.py", "command": "ls", "path": "src"}
    )
    pos_path = result.index("path=")
    pos_command = result.index("command=")
    pos_workdir = result.index("workdir=")
    pos_pattern = result.index("pattern=")
    assert pos_path < pos_command < pos_workdir < pos_pattern


def test_format_tool_input_total_length_bounded_for_many_values() -> None:
    big_input = {f"key{i}": "v" * 200 for i in range(5)}
    result = format_tool_input(big_input)
    # Each value truncated at 120 chars + "…" = 121, plus key names and separators
    # 5 * (121 + ~8) = ~645, well under a hard limit of 900
    assert len(result) < 900  # noqa: PLR2004
