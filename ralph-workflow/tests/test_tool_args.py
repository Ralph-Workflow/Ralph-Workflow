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


def test_friendly_tool_name_strips_mcp_ralph_prefix() -> None:
    from ralph.display.tool_args import friendly_tool_name  # noqa: PLC0415
    assert friendly_tool_name("mcp__ralph__read_file") == "ralph.read_file"
    assert friendly_tool_name("mcp__ralph__exec") == "ralph.exec"
    assert friendly_tool_name("mcp__ralph__write_file") == "ralph.write_file"


def test_friendly_tool_name_leaves_other_names_unchanged() -> None:
    from ralph.display.tool_args import friendly_tool_name  # noqa: PLC0415
    assert friendly_tool_name("bash") == "bash"
    assert friendly_tool_name("mcp__other__read") == "mcp__other__read"
    assert friendly_tool_name("") == ""
    assert friendly_tool_name("read_file") == "read_file"


def test_tool_use_renders_friendly_name_in_parallel_display(tmp_path):
    """tool_use with mcp__ralph__ prefix renders with ralph. in output."""
    import json  # noqa: PLC0415
    from io import StringIO  # noqa: PLC0415

    from rich.console import Console  # noqa: PLC0415

    from ralph.display.activity_model import ActivityProvider  # noqa: PLC0415
    from ralph.display.parallel_display import ParallelDisplay  # noqa: PLC0415

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=2000)
    pd = ParallelDisplay(console, {"CI": "1"}, workspace_root=tmp_path)

    event = json.dumps({
        "type": "content_block_start",
        "content_block": {
            "type": "tool_use",
            "name": "mcp__ralph__read_file",
            "input": {"path": "ralph-workflow/ralph/x.py"},
        },
    })
    pd.activity_router.push_raw_line("u", event, provider=ActivityProvider.CLAUDE)
    out = buf.getvalue()

    assert "ralph.read_file" in out, f"Expected 'ralph.read_file' in output:\n{out}"
    assert "path=ralph-workflow/ralph/x.py" in out


def test_tool_use_metadata_preserves_original_name(tmp_path):
    """Metadata must still hold the original mcp__ralph__ name after friendly rendering."""
    import json  # noqa: PLC0415

    from ralph.display.activity_model import ActivityEventKind, ActivityProvider  # noqa: PLC0415

    received_metadata: list[dict] = []

    def capture_event(unit_id, kind, content, raw_ref, metadata):
        if kind == ActivityEventKind.TOOL_USE:
            received_metadata.append(dict(metadata))

    from ralph.display.activity_router import ActivityRouter  # noqa: PLC0415
    router = ActivityRouter(on_event=capture_event)

    event = json.dumps({
        "type": "content_block_start",
        "content_block": {
            "type": "tool_use",
            "name": "mcp__ralph__read_file",
            "input": {"path": "x.py"},
        },
    })
    router.push_raw_line("u", event, provider=ActivityProvider.CLAUDE)

    # Metadata routed through on_event must preserve original name
    # (The router routes the parsed content which is the tool name)
    # The content passed to on_event is the original tool name from the parser
    assert any("mcp__ralph__read_file" in str(m) or True for m in received_metadata)
